from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from pydantic import BaseModel, Field
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import FlowAccount, FlowAccountHealthEvent, FlowDispatchEvent, Request
from flowboard.services.chrome_profile import (
    ChromeProfileLaunchError,
    launch_flow_account_profile,
)
from flowboard.services.flow_browser import FlowBrowserError, flow_browser
from flowboard.services.flow_client import flow_client

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreateBody(BaseModel):
    label: str
    provider: str = "flow"
    priority_weight: int = Field(default=100, ge=1, le=1000)


class AccountPatchBody(BaseModel):
    label: str | None = None
    status: str | None = None
    priority_weight: int | None = Field(default=None, ge=1, le=1000)
    credential: str | None = None
    paygate_tier: str | None = None
    credits: str | None = None


def _dump(row: FlowAccount) -> dict:
    data = row.model_dump(mode="json")
    credential = row.credential or ""
    data["credential_configured"] = bool(credential.strip())
    data.pop("credential", None)
    return data


@router.get("")
def list_accounts() -> list[dict]:
    with get_session() as s:
        rows = s.exec(select(FlowAccount).order_by(FlowAccount.created_at.desc())).all()
        return [_dump(row) for row in rows]


@router.post("")
def create_account(body: AccountCreateBody) -> dict:
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "label is required")
    provider = body.provider.strip() or "flow"
    with get_session() as s:
        row = FlowAccount(
            label=label,
            provider=provider,
            status="active",
            priority_weight=body.priority_weight,
            updated_at=datetime.now(timezone.utc),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"account": _dump(row)}


@router.patch("/{account_id}")
def patch_account(account_id: int, body: AccountPatchBody) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")

        if body.label is not None:
            label = body.label.strip()
            if not label:
                raise HTTPException(400, "label must not be empty")
            row.label = label
        if body.status is not None:
            status = body.status.strip().lower()
            if status not in {"active", "paused", "disabled", "unhealthy"}:
                raise HTTPException(400, "status is invalid")
            row.status = status
        if body.priority_weight is not None:
            row.priority_weight = body.priority_weight
        if body.credential is not None:
            cred = body.credential.strip()
            row.credential = cred or None
        if body.paygate_tier is not None:
            row.paygate_tier = (body.paygate_tier or "").strip() or None
        if body.credits is not None:
            row.credits = (body.credits or "").strip() or None

        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"account": _dump(row)}


@router.post("/{account_id}/test")
def test_account(account_id: int) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")

        # Placeholder health-check for Phase 2: account row is considered
        # reachable if it's not disabled. Real provider auth/token probe comes
        # in dispatcher + flow-client account scoping phase.
        ok = row.status != "disabled"
        message = "account is available" if ok else "account is disabled"

        event = FlowAccountHealthEvent(
            account_id=row.id or account_id,
            status="ok" if ok else "failed",
            message=message,
        )
        s.add(event)
        if ok:
            row.last_error = None
        else:
            row.last_error = message
        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        s.refresh(row)

        return {"ok": ok, "message": message, "account": _dump(row)}


@router.post("/{account_id}/capture-credential")
async def capture_account_credential(account_id: int) -> dict:
    with get_session() as s:
        if s.get(FlowAccount, account_id) is None:
            raise HTTPException(404, "account not found")

    token: str | None = None
    cdp_error: str | None = None
    try:
        flow_client.set_active_account(account_id)
        token = await flow_browser.capture_flow_token(account_id)
    except FlowBrowserError as exc:
        cdp_error = str(exc)

    if token:
        await flow_client.handle_message({"type": "token_captured", "flowKey": token})
        user_info = await flow_browser.fetch_user_info(token)
        if user_info:
            await flow_client.handle_message({"type": "user_info", "userInfo": user_info})

    if not token:
        detail = cdp_error or "browser_profile_not_connected"
        raise HTTPException(409, f"browser_token_not_available:{detail}")

    tier_refreshed = await flow_client.fetch_paygate_tier()

    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")
        row.credential = token.strip() or None
        user_info = flow_client.user_info or {}
        email = user_info.get("email") if isinstance(user_info, dict) else None
        if isinstance(email, str) and email.strip():
            row.email = email.strip().lower()
        tier = flow_client.paygate_tier
        if isinstance(tier, str) and tier.strip():
            row.paygate_tier = tier.strip()
        credits = flow_client.credits
        if credits is not None:
            row.credits = str(credits)
        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"account": _dump(row), "captured": True, "tier_refreshed": tier_refreshed}

@router.post("/{account_id}/open-profile")
def open_account_profile(account_id: int) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")

        try:
            launch = launch_flow_account_profile(account_id)
        except ChromeProfileLaunchError as exc:
            raise HTTPException(409, str(exc)) from exc

        row.chrome_user_data_dir = launch.get("profile_dir")
        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"ok": True, "account": _dump(row), "launch": launch}


@router.post("/{account_id}/cooldown/reset")
def reset_account_cooldown(account_id: int) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")
        row.cooldown_until = None
        row.last_error = None
        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"account": _dump(row)}


@router.delete("/{account_id}")
def disable_account(account_id: int) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")
        row.status = "disabled"
        row.updated_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        return {"ok": True}


@router.delete("/{account_id}/hard")
def hard_delete_account(account_id: int) -> dict:
    with get_session() as s:
        row = s.get(FlowAccount, account_id)
        if row is None:
            raise HTTPException(404, "account not found")
        try:
            s.exec(select(Request).where(Request.account_id == account_id))
            s.exec(select(FlowDispatchEvent).where(FlowDispatchEvent.account_id == account_id))
            s.exec(select(FlowAccountHealthEvent).where(FlowAccountHealthEvent.account_id == account_id))
            s.exec(select(FlowAccount).where(FlowAccount.id == account_id))

            s.exec(
                Request.__table__.update()
                .where(Request.account_id == account_id)
                .values(account_id=None)
            )
            s.exec(
                FlowDispatchEvent.__table__.update()
                .where(FlowDispatchEvent.account_id == account_id)
                .values(account_id=None)
            )
            s.exec(
                FlowAccountHealthEvent.__table__.delete()
                .where(FlowAccountHealthEvent.account_id == account_id)
            )
            s.exec(
                FlowAccount.__table__.delete()
                .where(FlowAccount.id == account_id)
            )
            s.commit()
            return {"ok": True}
        except SQLAlchemyError as exc:
            s.rollback()
            raise HTTPException(409, f"hard_delete_failed: {exc.__class__.__name__}") from exc
        except Exception as exc:  # noqa: BLE001
            s.rollback()
            raise HTTPException(500, f"hard_delete_unexpected: {exc}") from exc
