from pathlib import Path

import pytest

from flowboard.services.llm import secrets

@pytest.fixture
def tmp_secrets_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "secrets.json"
    monkeypatch.setenv("FLOWBOARD_SECRETS_PATH", str(p))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return p

def test_elevenlabs_status_missing(client, tmp_secrets_path):
    resp = client.get("/api/elevenlabs/status")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "source": "missing"}

def test_elevenlabs_key_roundtrip(client, tmp_secrets_path):
    resp = client.put("/api/elevenlabs/key", json={"apiKey": "el-key"})
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert resp.json()["source"] == "local"
    assert secrets.get_api_key("elevenlabs") == "el-key"

    resp = client.get("/api/elevenlabs/status")
    assert resp.json() == {"configured": True, "source": "local"}

    resp = client.put("/api/elevenlabs/key", json={"apiKey": None})
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert secrets.get_api_key("elevenlabs") is None

def test_elevenlabs_status_prefers_env(client, tmp_secrets_path, monkeypatch):
    secrets.set_api_key("elevenlabs", "local-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    resp = client.get("/api/elevenlabs/status")
    assert resp.status_code == 200
    assert resp.json() == {"configured": True, "source": "env"}
