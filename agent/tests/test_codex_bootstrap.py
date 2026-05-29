from __future__ import annotations

import os
from pathlib import Path

import pytest

from flowboard.services.llm import codex_bootstrap
from flowboard.services.llm.cli_utils import (
    build_cli_env,
    get_codex_home,
    get_flowboard_node_paths,
)


class _RunResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_cli_env_includes_flowboard_portable_node(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    node_dir = tools / "node" / "node-v99.0.0-win-x64"
    bin_dir = tools / "codex" / "node_modules" / ".bin"
    node_dir.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (node_dir / "node.exe").write_text("", encoding="utf-8")
    (bin_dir / "codex.cmd").write_text("", encoding="utf-8")
    monkeypatch.setenv("FLOWBOARD_TOOLS_DIR", str(tools))

    assert str(node_dir.resolve()) in get_flowboard_node_paths()
    path = build_cli_env("codex")["PATH"]

    assert str(node_dir.resolve()) in path
    assert str(bin_dir.resolve()) in path

def test_build_cli_env_sets_codex_home(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    env = build_cli_env("codex")

    assert env["CODEX_HOME"] == str(codex_home)
    assert get_codex_home() == codex_home.resolve()


def test_bootstrap_uses_portable_node_path_when_probing_local_codex(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    node_dir = tools / "node" / "node-v99.0.0-win-x64"
    npm = node_dir / ("npm.cmd" if os.name == "nt" else "npm")
    node = node_dir / ("node.exe" if os.name == "nt" else "node")
    node_dir.mkdir(parents=True)
    npm.write_text("", encoding="utf-8")
    node.write_text("", encoding="utf-8")

    monkeypatch.setenv("FLOWBOARD_TOOLS_DIR", str(tools))
    monkeypatch.setattr(codex_bootstrap, "_system_npm_bin", lambda: None)
    monkeypatch.setattr(codex_bootstrap, "_install_portable_node", lambda: npm)
    monkeypatch.setattr(codex_bootstrap.shutil, "which", lambda *_a, **_kw: None)

    seen_probe_env: dict[str, str] = {}

    def fake_run(args, **kwargs):
        argv = [str(a) for a in args]
        if "install" in argv:
            codex = codex_bootstrap.bundled_codex_bin()
            codex.parent.mkdir(parents=True, exist_ok=True)
            codex.write_text("", encoding="utf-8")
            return _RunResult(stdout="installed")
        if argv[0].endswith("codex.cmd") or argv[0].endswith("/codex"):
            seen_probe_env["PATH"] = kwargs.get("env", {}).get("PATH", "")
            if str(node_dir.resolve()) in seen_probe_env["PATH"]:
                return _RunResult(stdout="codex 1.0.0")
            return _RunResult(returncode=1, stderr="node not found")
        if argv[0].endswith("npm.cmd") or argv[0].endswith("/npm"):
            return _RunResult(stdout="11.0.0")
        raise AssertionError(f"unexpected subprocess args: {argv}")

    monkeypatch.setattr(codex_bootstrap.subprocess, "run", fake_run)

    result = codex_bootstrap.bootstrap_codex_cli()

    assert result["ok"] is True
    assert result["status"]["codex_present"] is True
    assert str(node_dir.resolve()) in seen_probe_env["PATH"]

def test_status_reports_codex_not_logged_in(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    codex = tools / "codex" / "node_modules" / ".bin" / (
        "codex.cmd" if os.name == "nt" else "codex"
    )
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("", encoding="utf-8")
    monkeypatch.setenv("FLOWBOARD_TOOLS_DIR", str(tools))
    monkeypatch.setattr(codex_bootstrap, "_system_npm_bin", lambda: None)
    monkeypatch.setattr(codex_bootstrap.shutil, "which", lambda *_a, **_kw: None)

    def fake_run(args, **kwargs):
        argv = [str(a) for a in args]
        if argv[1:] == ["--version"]:
            return _RunResult(stdout="codex-cli 1.0.0")
        if argv[1:] == ["login", "status"]:
            return _RunResult(returncode=1, stdout="Not logged in")
        raise AssertionError(f"unexpected subprocess args: {argv}")

    monkeypatch.setattr(codex_bootstrap.subprocess, "run", fake_run)

    status = codex_bootstrap.codex_bootstrap_status()

    assert status["codex_present"] is True
    assert status["codex_login_state"] == "not_logged_in"

def test_reset_codex_login_moves_auth_files(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    auth = codex_home / "auth.json"
    creds = codex_home / "credentials.json"
    auth.write_text("auth", encoding="utf-8")
    creds.write_text("creds", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(
        codex_bootstrap,
        "codex_bootstrap_status",
        lambda: {"codex_login_state": "not_logged_in"},
    )

    result = codex_bootstrap.reset_codex_login()

    assert result["ok"] is True
    assert not auth.exists()
    assert not creds.exists()
    assert len(result["moved"]) == 2
    for moved in result["moved"]:
        assert Path(moved).exists()

def test_windows_login_launcher_uses_new_console_without_start_title(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows launcher behavior")
    tools = tmp_path / "tools"
    codex = tools / "codex" / "node_modules" / ".bin" / "codex.cmd"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("", encoding="utf-8")
    monkeypatch.setenv("FLOWBOARD_TOOLS_DIR", str(tools))
    seen: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

        class Proc:
            pass

        return Proc()

    monkeypatch.setattr(codex_bootstrap.subprocess, "Popen", fake_popen)

    codex_bootstrap._launch_windows_codex_login(codex, {"PATH": "C:/node"})

    assert seen["args"] == ["cmd.exe", "/d", "/c", str(tools / "codex-login.cmd")]
    assert "creationflags" in seen["kwargs"]
