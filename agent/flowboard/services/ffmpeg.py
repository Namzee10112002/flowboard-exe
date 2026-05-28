from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    pass


def _install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(os.getenv("FLOWBOARD_INSTALL_DIR", Path.cwd())).resolve()


def _candidate_tools(name: str) -> list[Path]:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "tools" / name)
        candidates.append(Path(bundle_root) / name)
    install_dir = _install_dir()
    candidates.append(install_dir / "tools" / name)
    candidates.append(install_dir / name)
    return candidates


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    configured = os.environ.get("FLOWBOARD_FFMPEG_BIN") or os.environ.get("IMAGEIO_FFMPEG_EXE")
    if configured:
        return configured

    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for candidate in _candidate_tools(exe_name):
        if candidate.is_file():
            return str(candidate)

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    path_value = shutil.which("ffmpeg")
    if path_value:
        return path_value

    raise FFmpegNotFoundError("ffmpeg_not_found")


def command(*args: str) -> list[str]:
    return [ffmpeg_exe(), *args]


def status() -> dict:
    try:
        exe = ffmpeg_exe()
    except FFmpegNotFoundError:
        return {"available": False, "path": None}
    return {"available": True, "path": exe}
