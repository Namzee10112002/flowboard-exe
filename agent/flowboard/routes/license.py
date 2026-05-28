from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.services.license import LicenseNetworkError, activate, get_status

router = APIRouter(prefix="/api/license", tags=["license"])


class ActivateLicenseBody(BaseModel):
    key: str


@router.get("/status")
def license_status() -> dict:
    return get_status(refresh=True).to_public_dict()


@router.post("/activate")
def activate_license(body: ActivateLicenseBody) -> dict:
    try:
        state = activate(body.key)
    except LicenseNetworkError as exc:
        raise HTTPException(status_code=503, detail="license_sheet_unavailable") from exc
    if not state.licensed:
        return state.to_public_dict()
    return state.to_public_dict()
