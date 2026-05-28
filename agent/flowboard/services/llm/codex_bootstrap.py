from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from flowboard.config import STORAGE_DIR
from flowboard.services.llm.cli_utils import CLI_PROBE_TIMEOUT, build_cli_env

NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
CODEX_PACKAGE = "@openai/codex@latest"


class CodexBootstrapError(RuntimeError):
    """Raised when Flowboard cannot install a local Codex CLI runtime."""


def tools_root() -> Path:
    return Path(os.getenv("FLOWBOARD_TOOLS_DIR", STORAGE_DIR / "tools")).resolve()


def codex_install_root() -> Path:
    return tools_root() / "codex"


def bundled_codex_bin() -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return codex_install_root() / "node_modules" / ".bin" / f"codex{suffix}"


def bundled_node_root() -> Path:
    return tools_root() / "node"


def _bundled_npm_bin() -> Path | None:
    node_root = bundled_node_root()
    suffix = ".cmd" if os.name == "nt" else ""
    candidates = sorted(node_root.glob(f"node-*/npm{suffix}"), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    npm = node_root / f"npm{suffix}"
    return npm if npm.is_file() else None


def _system_npm_bin() -> str | None:
    return shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")


def _probe_cmd(
    args: list[str],
    timeout: float = CLI_PROBE_TIMEOUT,
    env: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            text=True,
            env=env,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output or None


def codex_bootstrap_status() -> dict[str, Any]:
    npm_bin = _system_npm_bin() or (_bundled_npm_bin() and str(_bundled_npm_bin()))
    codex_candidates: list[str] = []
    if bundled_codex_bin().is_file():
        codex_candidates.append(str(bundled_codex_bin()))
    system_codex = shutil.which("codex")
    if system_codex and system_codex not in codex_candidates:
        codex_candidates.append(system_codex)
    codex_bin = codex_candidates[0] if codex_candidates else None
    codex_ok = False
    codex_version = None
    env = _bootstrap_env(npm_bin)
    for candidate in codex_candidates:
        ok, version = _probe_cmd([candidate, "--version"], env=env)
        if ok:
            codex_bin = candidate
            codex_ok = True
            codex_version = version
            break
        if codex_version is None:
            codex_version = version
    npm_version = None
    if npm_bin:
        _, npm_version = _probe_cmd([npm_bin, "--version"], env=env)
    return {
        "npm_present": bool(npm_bin),
        "npm_path": npm_bin,
        "npm_version": npm_version,
        "bundled_node_present": _bundled_npm_bin() is not None,
        "codex_present": codex_ok,
        "codex_path": codex_bin if codex_ok else None,
        "codex_version": codex_version,
        "codex_install_dir": str(codex_install_root()),
        "node_install_dir": str(bundled_node_root()),
    }


def bootstrap_codex_cli() -> dict[str, Any]:
    before = codex_bootstrap_status()
    if before["codex_present"]:
        return {"ok": True, "changed": False, "status": before}

    npm_bin = before["npm_path"]
    node_downloaded = False
    if not npm_bin:
        if os.name != "nt":
            raise CodexBootstrapError("npm_not_found")
        npm_bin = str(_install_portable_node())
        node_downloaded = True

    install_dir = codex_install_root()
    install_dir.mkdir(parents=True, exist_ok=True)
    env = _bootstrap_env(npm_bin)

    try:
        result = subprocess.run(
            [npm_bin, "install", "--prefix", str(install_dir), CODEX_PACKAGE],
            capture_output=True,
            text=True,
            timeout=240,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexBootstrapError("codex_install_timeout") from exc
    except OSError as exc:
        raise CodexBootstrapError(f"codex_install_failed: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()[:500]
        raise CodexBootstrapError(f"codex_install_failed: {stderr}")

    codex_bin = bundled_codex_bin()
    ok, version_or_error = _probe_cmd([str(codex_bin), "--version"], env=env)
    if not ok:
        after = codex_bootstrap_status()
        if not after["codex_present"]:
            detail = (version_or_error or "no output").strip()[:300]
            raise CodexBootstrapError(f"codex_installed_but_not_runnable: {detail}")
    after = codex_bootstrap_status()
    return {
        "ok": True,
        "changed": True,
        "node_downloaded": node_downloaded,
        "status": after,
    }

def _bootstrap_env(npm_bin: str | None = None) -> dict[str, str]:
    env = build_cli_env("codex")
    extra: list[str] = []
    if npm_bin:
        extra.append(str(Path(npm_bin).parent))
    path = env.get("PATH", "")
    parts = extra + ([path] if path else [])
    if parts:
        env["PATH"] = os.pathsep.join(parts)
    return env


def _install_portable_node() -> Path:
    version, arch_key = _select_node_release()
    zip_name = f"node-{version}-{arch_key}.zip"
    url = f"https://nodejs.org/dist/{version}/{zip_name}"
    node_root = bundled_node_root()
    node_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="flowboard-node-") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / zip_name
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir / "extract")
        extracted = tmp_dir / "extract" / f"node-{version}-{arch_key}"
        if not extracted.is_dir():
            raise CodexBootstrapError("node_zip_unexpected_layout")
        destination = node_root / extracted.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(extracted, destination)

    suffix = ".cmd" if os.name == "nt" else ""
    npm_bin = node_root / f"node-{version}-{arch_key}" / f"npm{suffix}"
    if not npm_bin.is_file():
        raise CodexBootstrapError("portable_npm_not_found")
    return npm_bin


def _select_node_release() -> tuple[str, str]:
    arch_key = _node_arch_key()
    with urllib.request.urlopen(NODE_INDEX_URL, timeout=20) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    if not isinstance(rows, list):
        raise CodexBootstrapError("node_index_invalid")
    desired_file = f"{arch_key}-zip"
    for prefer_lts in (True, False):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if prefer_lts and not row.get("lts"):
                continue
            version = row.get("version")
            files = row.get("files")
            if isinstance(version, str) and isinstance(files, list) and desired_file in files:
                return version, arch_key
    raise CodexBootstrapError(f"node_release_not_found_for_{arch_key}")


def _node_arch_key() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "win-x64"
    if machine in {"arm64", "aarch64"}:
        return "win-arm64"
    if machine in {"x86", "i386", "i686"}:
        return "win-x86"
    return "win-x64"
