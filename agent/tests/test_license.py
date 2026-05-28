from datetime import date, datetime, timezone

from flowboard.services import license as license_service


def _sheet(*rows: str) -> str:
    return "Key\tHWID\tStatus\tExpiry\tNote\n" + "\n".join(rows)


def test_verify_license_accepts_active_matching_hwid(monkeypatch):
    monkeypatch.setattr(license_service, "LICENSE_REQUIRED", True)

    state = license_service._verify_key(
        "123456",
        hwid="ABCDEF1234567890",
        fetcher=lambda: _sheet("123456\tABCDEF1234567890\tactive\t\tTest"),
        today=date(2026, 5, 21),
    )

    assert state.licensed is True
    assert state.status == "active"
    assert state.message == "license_valid"


def test_verify_license_rejects_wrong_hwid(monkeypatch):
    monkeypatch.setattr(license_service, "LICENSE_REQUIRED", True)

    state = license_service._verify_key(
        "123456",
        hwid="LOCALHWID",
        fetcher=lambda: _sheet("123456\tOTHERHWID\tactive\t\tTest"),
        today=date(2026, 5, 21),
    )

    assert state.licensed is False
    assert state.status == "hwid_mismatch"
    assert state.message == "license_hwid_mismatch"


def test_verify_license_rejects_expired_key(monkeypatch):
    monkeypatch.setattr(license_service, "LICENSE_REQUIRED", True)

    state = license_service._verify_key(
        "123456",
        hwid="ABCDEF1234567890",
        fetcher=lambda: _sheet("123456\tABCDEF1234567890\tactive\t2026-05-20\tTest"),
        today=date(2026, 5, 21),
    )

    assert state.licensed is False
    assert state.status == "expired"
    assert state.message == "license_expired"


def test_status_uses_valid_cache_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(license_service, "LICENSE_REQUIRED", True)
    monkeypatch.setattr(license_service, "LICENSE_CACHE_PATH", tmp_path / "license.json")
    monkeypatch.setenv("FLOWBOARD_LICENSE_HWID", "ABCDEF1234567890")

    state = license_service.LicenseState(
        required=True,
        licensed=True,
        hwid="ABCDEF1234567890",
        status="active",
        message="license_valid",
        checked_at=datetime.now(timezone.utc).isoformat(),
        source="sheet",
    )
    license_service._write_cache("123456", state)

    cached = license_service.get_status(refresh=False)

    assert cached.licensed is True
    assert cached.source == "cache"
    assert cached.key_masked == "12**56"
