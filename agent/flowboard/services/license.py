"""Google-Sheet backed license checks for packaged Flowboard builds.

The public sheet is expected to expose TSV columns:

    Key    HWID    Status    Expiry

Status must be ``active``. Expiry may be blank, an ISO date, a common
dd/mm/yyyy style date, or an Excel serial day number.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Callable, Optional

import httpx

from flowboard.config import (
    LICENSE_CACHE_PATH,
    LICENSE_HTTP_TIMEOUT_SECONDS,
    LICENSE_OFFLINE_GRACE_DAYS,
    LICENSE_REQUIRED,
    LICENSE_SHEET_URL,
)

logger = logging.getLogger(__name__)

_HWID_SALT = "flowboard-license-v1"
_ACTIVE_STATUS = "active"


class LicenseNetworkError(RuntimeError):
    """Raised when the license sheet cannot be reached."""


@dataclass
class LicenseState:
    required: bool
    licensed: bool
    hwid: str
    status: str
    message: str
    expiry: Optional[str] = None
    key_masked: Optional[str] = None
    checked_at: Optional[str] = None
    source: str = "none"
    offline_grace_until: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "required": self.required,
            "licensed": self.licensed,
            "hwid": self.hwid,
            "status": self.status,
            "message": self.message,
            "expiry": self.expiry,
            "keyMasked": self.key_masked,
            "checkedAt": self.checked_at,
            "source": self.source,
            "offlineGraceUntil": self.offline_grace_until,
        }


def current_hwid() -> str:
    """Return a stable, non-raw machine fingerprint for admin binding."""
    override = os.getenv("FLOWBOARD_LICENSE_HWID")
    if override:
        return _clean_hwid(override)

    parts = [_windows_machine_guid(), platform.node(), str(uuid.getnode())]
    seed = "|".join(p for p in parts if p)
    if not seed:
        seed = platform.platform()
    digest = hashlib.sha256(f"{_HWID_SALT}|{seed}".encode("utf-8")).hexdigest()
    return digest[:16].upper()


def get_status(*, refresh: bool = True) -> LicenseState:
    """Return current license state.

    ``refresh=True`` attempts an online check when a cached key exists.
    Middleware uses ``refresh=False`` so normal API calls never wait on the
    network; the frontend status call performs the online refresh at boot.
    """
    hwid = current_hwid()
    if not LICENSE_REQUIRED:
        return LicenseState(
            required=False,
            licensed=True,
            hwid=hwid,
            status="not_required",
            message="license_not_required",
            source="config",
        )

    cached = _read_cache()
    cached_key = _cache_key(cached)
    if cached_key and _cache_hwid(cached) == hwid and refresh:
        try:
            state = _verify_key(cached_key, hwid=hwid)
        except LicenseNetworkError:
            logger.warning("license: sheet unavailable, falling back to cache if valid")
        else:
            if state.licensed:
                _write_cache(cached_key, state)
            else:
                _clear_cache()
            return state

    cache_state = _state_from_cache(cached, hwid=hwid)
    if cache_state is not None:
        return cache_state

    return LicenseState(
        required=True,
        licensed=False,
        hwid=hwid,
        status="missing",
        message="missing_license_key",
        source="cache",
    )


def activate(key: str) -> LicenseState:
    clean_key = key.strip()
    if not clean_key:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=current_hwid(),
            status="missing",
            message="missing_license_key",
            source="input",
        )

    state = _verify_key(clean_key, hwid=current_hwid())
    if state.licensed:
        _write_cache(clean_key, state)
    else:
        _clear_cache()
    return state


def is_request_allowed_without_license(path: str) -> bool:
    """Routes the browser needs before activation."""
    if not LICENSE_REQUIRED:
        return True
    allowed_api = (
        path == "/api/health"
        or path == "/api/license/status"
        or path == "/api/license/activate"
    )
    if allowed_api:
        return True
    return not (path.startswith("/api/") or path.startswith("/media/"))


def _verify_key(
    key: str,
    *,
    hwid: str,
    fetcher: Optional[Callable[[], str]] = None,
    today: Optional[date] = None,
) -> LicenseState:
    clean_key = key.strip()
    now = datetime.now(timezone.utc)
    rows = _load_rows(fetcher=fetcher)
    matches = [row for row in rows if row.get("key", "").strip() == clean_key]
    if not matches:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=hwid,
            status="not_found",
            message="license_key_not_found",
            key_masked=_mask_key(clean_key),
            checked_at=now.isoformat(),
            source="sheet",
        )

    clean_hwid = _clean_hwid(hwid)
    exact_match = next(
        (row for row in matches if _clean_hwid(row.get("hwid", "")) == clean_hwid),
        None,
    )
    row = exact_match or matches[0]
    row_hwid = _clean_hwid(row.get("hwid", ""))
    if not row_hwid:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=clean_hwid,
            status="unbound",
            message="license_hwid_not_bound",
            key_masked=_mask_key(clean_key),
            checked_at=now.isoformat(),
            source="sheet",
        )
    if row_hwid != clean_hwid:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=clean_hwid,
            status="hwid_mismatch",
            message="license_hwid_mismatch",
            key_masked=_mask_key(clean_key),
            checked_at=now.isoformat(),
            source="sheet",
        )

    status = row.get("status", "").strip().lower()
    if status != _ACTIVE_STATUS:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=clean_hwid,
            status=status or "inactive",
            message="license_inactive",
            key_masked=_mask_key(clean_key),
            checked_at=now.isoformat(),
            source="sheet",
        )

    expiry = _parse_expiry(row.get("expiry", ""))
    today_value = today or datetime.now(timezone.utc).date()
    if expiry is not None and expiry < today_value:
        return LicenseState(
            required=LICENSE_REQUIRED,
            licensed=False,
            hwid=clean_hwid,
            status="expired",
            message="license_expired",
            expiry=expiry.isoformat(),
            key_masked=_mask_key(clean_key),
            checked_at=now.isoformat(),
            source="sheet",
        )

    return LicenseState(
        required=LICENSE_REQUIRED,
        licensed=True,
        hwid=clean_hwid,
        status="active",
        message="license_valid",
        expiry=expiry.isoformat() if expiry else None,
        key_masked=_mask_key(clean_key),
        checked_at=now.isoformat(),
        source="sheet",
    )


def _load_rows(*, fetcher: Optional[Callable[[], str]] = None) -> list[dict[str, str]]:
    text = fetcher() if fetcher else _fetch_sheet()
    reader = csv.DictReader(StringIO(text), dialect="excel-tab")
    rows: list[dict[str, str]] = []
    for row in reader:
        normalized: dict[str, str] = {}
        for k, v in row.items():
            if k is None:
                continue
            normalized[k.strip().lower()] = (v or "").strip()
        if normalized:
            rows.append(normalized)
    return rows


def _fetch_sheet() -> str:
    try:
        with httpx.Client(timeout=LICENSE_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = client.get(LICENSE_SHEET_URL)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        raise LicenseNetworkError(str(exc)) from exc


def _state_from_cache(cache: dict, *, hwid: str) -> LicenseState | None:
    key = _cache_key(cache)
    if not key or _cache_hwid(cache) != hwid:
        return None
    if cache.get("status") != "active":
        return None
    expiry = _parse_expiry(str(cache.get("expiry") or ""))
    today = datetime.now(timezone.utc).date()
    if expiry is not None and expiry < today:
        return None

    checked_at = str(cache.get("checked_at") or "")
    grace_until = _offline_grace_until(checked_at)
    if grace_until is None or datetime.now(timezone.utc) > grace_until:
        return None

    return LicenseState(
        required=True,
        licensed=True,
        hwid=hwid,
        status="active",
        message="license_valid_cached",
        expiry=expiry.isoformat() if expiry else None,
        key_masked=_mask_key(key),
        checked_at=checked_at or None,
        source="cache",
        offline_grace_until=grace_until.isoformat(),
    )


def _read_cache(path: Optional[Path] = None) -> dict:
    path = path or LICENSE_CACHE_PATH
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("license: cache unreadable, ignoring (%s)", exc)
        return {}


def _write_cache(key: str, state: LicenseState, path: Optional[Path] = None) -> None:
    path = path or LICENSE_CACHE_PATH
    payload = {
        "key": key,
        "hwid": state.hwid,
        "status": state.status,
        "expiry": state.expiry,
        "checked_at": state.checked_at or datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _clear_cache(path: Optional[Path] = None) -> None:
    path = path or LICENSE_CACHE_PATH
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("license: failed to clear cache (%s)", exc)


def _cache_key(cache: dict) -> str:
    value = cache.get("key")
    return value.strip() if isinstance(value, str) else ""


def _cache_hwid(cache: dict) -> str:
    value = cache.get("hwid")
    return _clean_hwid(value) if isinstance(value, str) else ""


def _offline_grace_until(checked_at: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt + timedelta(days=LICENSE_OFFLINE_GRACE_DAYS)


def _parse_expiry(raw: str) -> Optional[date]:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.isdigit():
        # Excel serial date, accounting for the 1900 leap-year bug.
        serial = int(value)
        if serial > 59:
            serial -= 1
        return date(1899, 12, 31) + timedelta(days=serial)

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _mask_key(key: str) -> str:
    clean = key.strip()
    if len(clean) <= 4:
        return "*" * len(clean)
    return f"{clean[:2]}{'*' * max(2, len(clean) - 4)}{clean[-2:]}"


def _clean_hwid(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())[:32]


def _windows_machine_guid() -> str:
    if platform.system().lower() != "windows":
        return ""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except Exception:
        return ""
