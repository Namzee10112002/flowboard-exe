from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import FlowAccount, FlowDispatchEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pick_account_for_request(request_id: int, reason: str = "auto") -> int | None:
    now = _utcnow()
    with get_session() as s:
        rows = s.exec(
            select(FlowAccount)
            .where(FlowAccount.status == "active")
            .order_by(FlowAccount.priority_weight.desc(), FlowAccount.updated_at.asc())
        ).all()
        eligible = [
            row for row in rows
            if row.cooldown_until is None or row.cooldown_until <= now
        ]
        picked = eligible[0] if eligible else None
        event = FlowDispatchEvent(
            request_id=request_id,
            account_id=picked.id if picked is not None else None,
            attempt_no=1,
            outcome="picked" if picked is not None else "failed",
            decision_reason=reason if picked is not None else "no_eligible_account",
            error_code=None if picked is not None else "no_eligible_account",
        )
        s.add(event)
        s.commit()
        return picked.id if picked is not None else None


def mark_dispatch_outcome(
    request_id: int,
    account_id: int | None,
    attempt_no: int,
    *,
    outcome: str,
    reason: str,
    error_code: str | None = None,
) -> None:
    with get_session() as s:
        event = FlowDispatchEvent(
            request_id=request_id,
            account_id=account_id,
            attempt_no=attempt_no,
            outcome=outcome,
            decision_reason=reason,
            error_code=error_code,
        )
        s.add(event)
        s.commit()
