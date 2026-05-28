"""Flowboard updater.

Default mode downloads the newest Windows release zip from GitHub Releases and
copies it over the current install directory. If the updater is launched from a
source checkout, ``--source`` performs a safe ``git pull --ff-only`` instead.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_REPO = "Namzee10112002/flowboard-exe"
DEFAULT_ASSET_HINTS = ("flowboard", "windows")
SELF_NAMES = {"update.exe", "flowboardupdater.exe"}

def _default_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    install_dir = args.install_dir.resolve()
    config = _read_config(install_dir)
    repo = args.repo or os.environ.get("FLOWBOARD_UPDATE_REPO") or config.get("repo") or DEFAULT_REPO

    if args.source or (install_dir / ".git").is_dir():
        return _update_source_checkout(install_dir)

    asset_name = args.asset or config.get("asset")
    print(f"Checking latest release for {repo}...")
    _write_status(install_dir, "checking", repo=repo)
    release = _github_json(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = str(release.get("tag_name") or "latest")
    asset = _select_asset(release, asset_name)
    if asset is None:
        _write_status(install_dir, "failed", repo=repo, tag=tag, message="No Windows zip asset found.")
        print("No Windows zip asset found on the latest release.")
        return 2

    download_url = str(asset["browser_download_url"])
    name = str(asset["name"])
    print(f"Downloading {name} ({tag})...")
    _write_status(install_dir, "downloading", repo=repo, tag=tag, asset=name)

    with tempfile.TemporaryDirectory(prefix="flowboard-update-") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / name
        _download(download_url, zip_path)
        extract_dir = tmp_dir / "extract"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        payload_dir = _payload_root(extract_dir)
        if args.dry_run:
            print(f"Dry run: would copy {payload_dir} -> {install_dir}")
            _write_status(install_dir, "dry_run", repo=repo, tag=tag, asset=name)
            return 0
        _write_status(install_dir, "applying", repo=repo, tag=tag, asset=name)
        try:
            _apply_payload(payload_dir, install_dir)
        except SystemExit as exc:
            _write_status(install_dir, "failed", repo=repo, tag=tag, asset=name, message=str(exc))
            raise

    _write_status(install_dir, "success", repo=repo, tag=tag, asset=name)
    print(f"Updated Flowboard to {tag}.")
    print("Start Flowboard.exe again if it is not already open.")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Flowboard from GitHub Releases.")
    parser.add_argument("--repo", help="GitHub repo in owner/name form.")
    parser.add_argument("--asset", help="Exact release asset name to download.")
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=_default_install_dir(),
        help="Directory containing Flowboard.exe and update.exe.",
    )
    parser.add_argument("--source", action="store_true", help="Run git pull in a source checkout.")
    parser.add_argument("--dry-run", action="store_true", help="Check and download without copying.")
    return parser.parse_args(argv)


def _read_config(install_dir: Path) -> dict[str, str]:
    path = install_dir / "update.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def _github_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Flowboard-Updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit("No published release found for this repository.") from exc
        raise


def _select_asset(release: dict[str, Any], exact_name: str | None) -> dict[str, Any] | None:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    candidates = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).endswith(".zip")]
    if exact_name:
        exact = [a for a in candidates if a.get("name") == exact_name]
        if exact:
            return exact[0]
    hinted = [
        a
        for a in candidates
        if all(hint in str(a.get("name", "")).lower() for hint in DEFAULT_ASSET_HINTS)
    ]
    return hinted[0] if hinted else (candidates[0] if candidates else None)


def _download(url: str, destination: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Flowboard-Updater"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with destination.open("wb") as f:
            shutil.copyfileobj(resp, f)

def _write_status(install_dir: Path, status: str, **fields: Any) -> None:
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **{k: v for k, v in fields.items() if v is not None},
    }
    try:
        (install_dir / "update-status.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _payload_root(extract_dir: Path) -> Path:
    children = [p for p in extract_dir.iterdir() if p.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_dir


def _apply_payload(payload_dir: Path, install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    for item in payload_dir.iterdir():
        if item.name.lower() in SELF_NAMES:
            print(f"Skipping self-update of {item.name}; keep the existing updater.")
            continue
        target = install_dir / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            try:
                shutil.copy2(item, target)
            except PermissionError as exc:
                raise SystemExit(
                    f"Could not replace {target.name}. Close Flowboard.exe and run update.exe again."
                ) from exc


def _update_source_checkout(path: Path) -> int:
    if not (path / ".git").is_dir():
        print(f"{path} is not a git checkout.")
        return 2
    print(f"Updating source checkout in {path}...")
    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=path,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    if result.returncode != 0:
        return result.returncode
    print("Source checkout updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
