from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from flowboard.config import ROOT, STORAGE_DIR
from flowboard.services.flow_browser import cdp_port_for_account

FLOW_URL = "https://labs.google/fx/tools/flow"


class ChromeProfileLaunchError(RuntimeError):
    """Raised when Flowboard cannot launch a browser profile for Flow."""


def _install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(os.getenv("FLOWBOARD_INSTALL_DIR", ROOT)).resolve()


def resolve_extension_dir() -> Path:
    override = os.getenv("FLOWBOARD_EXTENSION_DIR")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.extend([
        _install_dir() / "extension",
        ROOT / "extension",
    ])
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "manifest.json").is_file() and (resolved / "background.js").is_file():
            return resolved
    searched = ", ".join(str(p) for p in candidates)
    raise ChromeProfileLaunchError(f"extension_dir_not_found: {searched}")


def chrome_profile_root() -> Path:
    return Path(
        os.getenv("FLOWBOARD_CHROME_PROFILE_ROOT", STORAGE_DIR / "chrome-profiles")
    ).resolve()


def profile_dir_for_account(account_id: int) -> Path:
    if account_id <= 0:
        raise ChromeProfileLaunchError("account_id_must_be_positive")
    return chrome_profile_root() / f"account-{account_id}"


def _candidate_browser_paths() -> list[Path]:
    candidates: list[Path] = []
    override = os.getenv("FLOWBOARD_CHROME_PATH")
    if override:
        candidates.append(Path(override))

    for name in (
        "chrome",
        "chrome.exe",
        "msedge",
        "msedge.exe",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    env_dirs = [
        os.getenv("PROGRAMFILES"),
        os.getenv("PROGRAMFILES(X86)"),
        os.getenv("LOCALAPPDATA"),
    ]
    relative_paths = [
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Microsoft/Edge/Application/msedge.exe"),
    ]
    for base in env_dirs:
        if not base:
            continue
        for rel in relative_paths:
            candidates.append(Path(base) / rel)

    return candidates


def find_chrome_executable() -> Path:
    seen: set[Path] = set()
    for candidate in _candidate_browser_paths():
        resolved = candidate.expanduser()
        try:
            resolved = resolved.resolve()
        except OSError:
            pass
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    raise ChromeProfileLaunchError("chrome_not_found")


def launch_flow_account_profile(account_id: int) -> dict[str, Any]:
    browser_path = find_chrome_executable()
    cdp_port = cdp_port_for_account(account_id)
    profile_dir = profile_dir_for_account(account_id)
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = [
        str(browser_path),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={cdp_port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        FLOW_URL,
    ]

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(args, **popen_kwargs)
    except OSError as exc:
        raise ChromeProfileLaunchError(f"chrome_launch_failed: {exc}") from exc

    return {
        "pid": proc.pid,
        "browser_path": str(browser_path),
        "profile_dir": str(profile_dir),
        "extension_dir": None,
        "bridge": "cdp",
        "cdp_port": cdp_port,
        "cdp_url": f"http://127.0.0.1:{cdp_port}",
        "url": FLOW_URL,
    }
