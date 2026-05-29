"""Shared utilities for LLM CLI providers (Claude, Gemini, OpenAI Codex).

Consolidates cross-provider patterns:
- Binary path resolution (PATH + Windows npm fallback)
- Subprocess error handling
- Input validation (prompt size, attachment limits)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, Type

logger = logging.getLogger(__name__)

# Subprocess timeouts
CLI_PROBE_TIMEOUT = 5.0
DEFAULT_SUBPROCESS_TIMEOUT = 90.0

# Input validation limits
MAX_PROMPT_BYTES = 100 * 1024  # 100 KB
MAX_ATTACHMENTS = 10

# Windows npm paths
_WINDOWS_NPM_PATHS = [
    ("APPDATA", "npm"),
    ("USERPROFILE", "AppData", "Roaming", "npm"),
    ("HOME", "AppData", "Roaming", "npm"),
]


def get_windows_npm_paths(cli_name: str) -> list[str]:
    r"""Get dynamic list of Windows npm paths for a CLI tool.

    Checks:
    1. %APPDATA%\npm\<cli_name>.cmd
    2. %USERPROFILE%\AppData\Roaming\npm\<cli_name>.cmd
    3. ~\AppData\Roaming\npm\<cli_name>.cmd (via expanduser)

    Returns list of paths to check (may be empty if no env vars set).
    """
    paths = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "npm", f"{cli_name}.cmd"))

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        paths.append(
            os.path.join(userprofile, "AppData", "Roaming", "npm", f"{cli_name}.cmd")
        )

    home = os.path.expanduser("~")
    if home and home != "~":  # expanduser returns ~ if HOME not set
        paths.append(os.path.join(home, "AppData", "Roaming", "npm", f"{cli_name}.cmd"))

    return paths

def get_flowboard_tool_paths(cli_name: str) -> list[str]:
    """Return CLI shims installed into Flowboard's private tools dir."""
    try:
        from flowboard.config import STORAGE_DIR
    except Exception:  # noqa: BLE001
        return []
    root = Path(os.getenv("FLOWBOARD_TOOLS_DIR", STORAGE_DIR / "tools"))
    suffix = ".cmd" if os.name == "nt" else ""
    return [
        str(root / cli_name / "node_modules" / ".bin" / f"{cli_name}{suffix}"),
    ]


def get_flowboard_node_paths() -> list[str]:
    """Return portable Node directories managed by Flowboard.

    npm-generated Windows shims execute ``node`` from PATH unless a sibling
    ``node.exe`` exists next to the shim. Flowboard's portable Node lives in
    ``storage/tools/node/node-*``, so probes and real dispatches must prepend
    those directories when they target private tools.
    """
    try:
        from flowboard.config import STORAGE_DIR
    except Exception:  # noqa: BLE001
        return []
    root = Path(os.getenv("FLOWBOARD_TOOLS_DIR", STORAGE_DIR / "tools")) / "node"
    pattern = "node-*/node.exe" if os.name == "nt" else "node-*/bin/node"
    candidates = [node_exe.parent for node_exe in root.glob(pattern)]
    direct = root / ("node.exe" if os.name == "nt" else "bin/node")
    if direct.is_file():
        candidates.append(direct.parent)
    return [str(path.resolve()) for path in candidates if path.is_dir()]


def build_cli_env(cli_name: str) -> dict[str, str]:
    """Subprocess environment that can run Flowboard-managed CLI shims."""
    env = os.environ.copy()
    prepended: list[str] = []
    for tool_path in get_flowboard_tool_paths(cli_name):
        parent = Path(tool_path).parent
        if parent.is_dir():
            prepended.append(str(parent.resolve()))
    prepended.extend(get_flowboard_node_paths())

    seen: set[str] = set()
    clean: list[str] = []
    for path in prepended:
        key = path.lower() if os.name == "nt" else path
        if key in seen:
            continue
        seen.add(key)
        clean.append(path)
    if clean:
        env["PATH"] = os.pathsep.join(clean + [env.get("PATH", "")])
    return env


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Hide background CLI helper windows when Flowboard runs as a GUI app."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def resolve_cli_binary(
    cli_name: str, timeout: float = CLI_PROBE_TIMEOUT
) -> str:
    """Resolve CLI binary path: try PATH first, then Windows npm locations.

    Args:
        cli_name: Name of the CLI tool (e.g., "claude", "gemini", "codex")
        timeout: Timeout for --version probe (seconds)

    Returns:
        Resolved binary path, or cli_name as fallback (will error later if not found)
    """
    # Try PATH first
    if cli_path := shutil.which(cli_name):
        logger.debug(f"{cli_name}: resolved from PATH: {cli_path}")
        return cli_path

    for tool_path in get_flowboard_tool_paths(cli_name):
        if os.path.exists(tool_path):
            try:
                result = subprocess.run(
                    [tool_path, "--version"],
                    capture_output=True,
                    timeout=timeout,
                    env=build_cli_env(cli_name),
                    **hidden_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    logger.info(f"{cli_name}: resolved from Flowboard tools: {tool_path}")
                    return tool_path
            except (subprocess.TimeoutExpired, Exception):
                pass

    # Try Windows npm locations
    for npm_path in get_windows_npm_paths(cli_name):
        if os.path.exists(npm_path):
            try:
                result = subprocess.run(
                    [npm_path, "--version"],
                    capture_output=True,
                    timeout=timeout,
                    **hidden_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    logger.info(f"{cli_name}: resolved from npm: {npm_path}")
                    return npm_path
            except (subprocess.TimeoutExpired, Exception):
                pass

    logger.warning(
        f"{cli_name}: not found in PATH or npm locations, falling back to '{cli_name}'"
    )
    return cli_name


def validate_prompt_size(prompt: str, max_bytes: int = MAX_PROMPT_BYTES) -> None:
    """Validate prompt doesn't exceed size limit.

    Args:
        prompt: The user or system prompt
        max_bytes: Maximum allowed size in bytes

    Raises:
        ValueError: If prompt exceeds limit
    """
    if len(prompt.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"Prompt exceeds {max_bytes // 1024}KB limit "
            f"({len(prompt.encode('utf-8'))} bytes)"
        )


def validate_attachment_paths(
    attachments: Optional[list[str]], max_count: int = MAX_ATTACHMENTS
) -> None:
    """Validate attachments exist and are readable.

    Args:
        attachments: List of file paths
        max_count: Maximum number of attachments allowed

    Raises:
        ValueError: If validation fails
    """
    if not attachments:
        return

    if len(attachments) > max_count:
        raise ValueError(f"Too many attachments (max {max_count}, got {len(attachments)})")

    for path in attachments:
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            raise ValueError(f"Attachment not found: {path}")
        if not os.access(abs_path, os.R_OK):
            raise ValueError(f"Attachment not readable: {path}")


def validate_model_name(
    model: Optional[str], allowed: Optional[set[str]] = None
) -> Optional[str]:
    """Validate model name against whitelist.

    Args:
        model: Model name from user input or environment
        allowed: Set of allowed model names (if None, validation skipped)

    Returns:
        Validated model name, or None if invalid (caller should use default)

    Raises:
        ValueError: If validation is strict and model not allowed
    """
    if not model or not allowed:
        return model
    if model in allowed:
        return model
    logger.warning(f"Unknown model '{model}', not in allowed set: {allowed}")
    return None
