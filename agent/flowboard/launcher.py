"""Single-process launcher used by the packaged Windows executable."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def main() -> int:
    _configure_packaged_defaults()
    _configure_logging()
    host = "127.0.0.1"
    port = int(os.environ.get("FLOWBOARD_HTTP_PORT", "8101"))
    url = f"http://{host}:{port}"

    if not _port_available(host, port):
        print(f"Flowboard already appears to be running on {url}")
        _open_ui(url)
        return 0

    import uvicorn
    config = uvicorn.Config(
        "flowboard.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("FLOWBOARD_LOG_LEVEL", "info"),
        log_config=None,
        timeout_graceful_shutdown=2,
    )

    if _ui_disabled():
        uvicorn.Server(config).run()
        return 0

    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="flowboard-server", daemon=True)
    server_thread.start()
    _wait_until_ready(url)

    if _desktop_enabled() and _open_desktop_window(url):
        server.should_exit = True
        server_thread.join(timeout=5.0)
        return 0

    _open_browser(url)
    server_thread.join()
    return 0


def _configure_packaged_defaults() -> None:
    storage_dir = _default_storage_dir()
    os.environ.setdefault("FLOWBOARD_STORAGE", str(storage_dir))
    os.environ.setdefault("FLOWBOARD_LICENSE_REQUIRED", "1")
    os.environ.setdefault("FLOWBOARD_HTTP_PORT", "8101")


def _default_storage_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Flowboard" / "storage"
    return Path.home() / ".flowboard" / "storage"


def _log_dir() -> Path:
    storage = Path(os.environ.get("FLOWBOARD_STORAGE", _default_storage_dir()))
    return storage.parent / "logs"

def _configure_logging() -> None:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "launcher.log", encoding="utf-8"),
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=os.environ.get("FLOWBOARD_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

def _write_crash_log() -> None:
    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "launcher-crash.log").open("a", encoding="utf-8") as fh:
            fh.write("\n--- Flowboard launcher crash ---\n")
            fh.write(traceback.format_exc())
    except Exception:
        pass

def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def _ui_disabled() -> bool:
    return os.environ.get("FLOWBOARD_NO_BROWSER", "").lower() in {"1", "true", "yes"}


def _desktop_enabled() -> bool:
    return os.environ.get("FLOWBOARD_DESKTOP_APP", "1").lower() not in {"0", "false", "no"}


def _wait_until_ready(url: str) -> bool:
    health_url = f"{url}/api/health"
    for _ in range(80):
        try:
            with urllib.request.urlopen(health_url, timeout=0.5):
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def _open_ui(url: str) -> None:
    if not _ui_disabled() and _desktop_enabled() and _open_desktop_window(url):
        return
    if not _ui_disabled():
        _open_browser(url)


def _open_desktop_window(url: str) -> bool:
    try:
        import webview
    except Exception:
        logging.getLogger(__name__).exception("pywebview is not available; falling back to browser")
        return False
    try:
        webview.create_window(
            "Flowboard",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            text_select=True,
        )
        webview.start(debug=os.environ.get("FLOWBOARD_WEBVIEW_DEBUG") == "1")
        return True
    except Exception:
        logging.getLogger(__name__).exception("desktop webview failed; falling back to browser")
        return False


def _open_browser(url: str) -> None:
    webbrowser.open(url)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        _write_crash_log()
        raise
