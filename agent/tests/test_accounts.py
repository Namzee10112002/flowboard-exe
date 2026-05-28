from __future__ import annotations

from flowboard.routes import accounts as accounts_route
from flowboard.services.chrome_profile import ChromeProfileLaunchError


def test_open_profile_launches_browser_and_persists_profile_dir(client, monkeypatch, tmp_path):
    created = client.post(
        "/api/accounts",
        json={"label": "Flow A", "provider": "flow", "priority_weight": 100},
    )
    assert created.status_code == 200
    account_id = created.json()["account"]["id"]
    profile_dir = tmp_path / "chrome-profile"

    def fake_launch(seen_account_id: int):
        assert seen_account_id == account_id
        return {
            "pid": 1234,
            "browser_path": "C:/Chrome/chrome.exe",
            "profile_dir": str(profile_dir),
            "extension_dir": "C:/flowboard/extension",
            "url": "https://labs.google/fx/tools/flow",
        }

    monkeypatch.setattr(accounts_route, "launch_flow_account_profile", fake_launch)

    resp = client.post(f"/api/accounts/{account_id}/open-profile")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["launch"]["pid"] == 1234
    assert body["account"]["chrome_user_data_dir"] == str(profile_dir)

    listed = client.get("/api/accounts").json()
    assert listed[0]["chrome_user_data_dir"] == str(profile_dir)


def test_open_profile_returns_404_for_missing_account(client):
    resp = client.post("/api/accounts/999/open-profile")
    assert resp.status_code == 404


def test_open_profile_returns_409_when_browser_cannot_launch(client, monkeypatch):
    created = client.post("/api/accounts", json={"label": "Flow A"})
    account_id = created.json()["account"]["id"]

    def fake_launch(_account_id: int):
        raise ChromeProfileLaunchError("chrome_not_found")

    monkeypatch.setattr(accounts_route, "launch_flow_account_profile", fake_launch)

    resp = client.post(f"/api/accounts/{account_id}/open-profile")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "chrome_not_found"
