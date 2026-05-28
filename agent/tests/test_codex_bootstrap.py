from __future__ import annotations

import os
from pathlib import Path

from flowboard.services.llm import codex_bootstrap
from flowboard.services.llm.cli_utils import build_cli_env, get_flowboard_node_paths


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
