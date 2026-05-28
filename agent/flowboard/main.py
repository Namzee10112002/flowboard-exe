import asyncio
import hmac
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from flowboard import __version__
from flowboard.config import ROOT, WS_HOST
from flowboard.db import get_session, init_db
from flowboard.db.models import Request
from flowboard.routes import (
    accounts,
    activity,
    auth,
    boards,
    chat,
    edges,
    elevenlabs,
    flow_projects,
    license,
    llm,
    media,
    nodes,
    plans,
    projects,
    prompt,
    scenarios,
    upload,
    vision,
)
from flowboard.routes import references as references_route
from flowboard.routes import requests as requests_route
from flowboard.services.flow_client import flow_client
from flowboard.services import ffmpeg as ffmpeg_service
from flowboard.services.license import (
    current_hwid,
    get_status,
    is_request_allowed_without_license,
)
from flowboard.services.ws_server import run_ws_server
from flowboard.worker.processor import get_worker

# Guard rail: the dedicated WS server is unauthenticated and would expose the
# callback secret to any process that can reach it. Refuse to boot if someone
# overrode WS_HOST to a non-loopback address.
if WS_HOST not in ("127.0.0.1", "localhost", "::1"):
    raise RuntimeError(
        f"FLOWBOARD_WS_HOST must be loopback (got {WS_HOST!r}); the extension WS "
        "is unauthenticated by design and must not be network-reachable."
    )

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def _install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(os.getenv("FLOWBOARD_INSTALL_DIR", ROOT)).resolve()

def _read_local_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None

def _build_info() -> dict:
    info = _read_local_json(_install_dir() / "build-info.json") or {}
    out = {str(k): v for k, v in info.items() if isinstance(k, str)}
    out["version"] = str(out.get("version") or __version__)
    out["codex_git_repo_check_skipped"] = True
    return out

def _update_status() -> dict | None:
    data = _read_local_json(_install_dir() / "update-status.json")
    if data is None:
        return None
    return {str(k): v for k, v in data.items() if isinstance(k, str)}


def _recover_orphan_running_requests() -> int:
    """Mark any pre-existing 'running' requests as failed so a restart doesn't
    leave nodes polling a request that nobody is processing anymore."""
    from datetime import datetime, timezone
    from sqlmodel import select as _select

    touched = 0
    with get_session() as s:
        rows = s.exec(_select(Request).where(Request.status == "running")).all()
        for r in rows:
            r.status = "failed"
            r.error = "agent_restart_lost"
            r.finished_at = datetime.now(timezone.utc)
            s.add(r)
            touched += 1
        if touched:
            s.commit()
    return touched


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recovered = _recover_orphan_running_requests()
    if recovered:
        logger.info("recovered %d orphan running request(s) → failed", recovered)
    worker = get_worker()
    ws_task = asyncio.create_task(run_ws_server(), name="ext-ws-server")
    worker_task = asyncio.create_task(worker.start(), name="request-worker")
    logger.info("flowboard agent started (ws:9223 + worker)")
    try:
        yield
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(worker.drain(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("worker drain timed out")
        for t in (ws_task, worker_task):
            t.cancel()
        await asyncio.gather(ws_task, worker_task, return_exceptions=True)
        logger.info("flowboard agent stopped")


app = FastAPI(title="Flowboard Agent", version="0.0.2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def license_middleware(request: FastAPIRequest, call_next):
    path = request.url.path
    if is_request_allowed_without_license(path):
        return await call_next(request)
    state = get_status(refresh=False)
    if state.licensed:
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={
            "detail": "license_required",
            "hwid": current_hwid(),
        },
    )


app.include_router(boards.router)
app.include_router(nodes.router)
app.include_router(edges.router)
app.include_router(chat.router)
app.include_router(projects.router)
app.include_router(flow_projects.router)
app.include_router(references_route.router)
app.include_router(requests_route.router)
app.include_router(media.bytes_router)
app.include_router(media.api_router)
app.include_router(upload.router)
app.include_router(plans.router)
app.include_router(vision.router)
app.include_router(prompt.router)
app.include_router(scenarios.router)
app.include_router(auth.router)
app.include_router(license.router)
app.include_router(elevenlabs.router)
app.include_router(llm.router)
app.include_router(activity.router)
app.include_router(accounts.router)


def _frontend_dist_dir() -> Path | None:
    candidates: list[Path] = []
    override = os.getenv("FLOWBOARD_FRONTEND_DIST")
    if override:
        candidates.append(Path(override))
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.extend(
            [
                Path(bundle_root) / "frontend_dist",
                Path(bundle_root) / "frontend" / "dist",
            ]
        )
    candidates.append(ROOT / "frontend" / "dist")

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def _mount_frontend_static() -> None:
    dist_dir = _frontend_dist_dir()
    if dist_dir is None:
        logger.info("frontend static build not found; API-only mode")
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str = ""):
        if full_path.startswith(("api/", "media/", "ws/")):
            raise HTTPException(status_code=404, detail="not found")
        target = (dist_dir / full_path).resolve()
        try:
            target.relative_to(dist_dir)
        except ValueError:
            raise HTTPException(status_code=404, detail="not found")
        if target.is_file():
            return FileResponse(target)
        return FileResponse(dist_dir / "index.html")

    logger.info("serving frontend static build from %s", dist_dir)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "app_version": __version__,
        "build": _build_info(),
        "update_status": _update_status(),
        "tools": {
            "ffmpeg": ffmpeg_service.status(),
        },
        "extension_connected": flow_client.connected,
        "ws_stats": flow_client.ws_stats,
    }


@app.post("/api/ext/callback")
async def ext_callback(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
) -> dict:
    """HTTP callback for the extension to deliver API responses."""
    if not x_callback_secret or not hmac.compare_digest(
        x_callback_secret, flow_client.callback_secret
    ):
        raise HTTPException(status_code=401, detail="invalid callback secret")

    try:
        payload = await body.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")

    if not isinstance(payload, dict) or "id" not in payload:
        raise HTTPException(status_code=400, detail="missing id")

    matched = flow_client.resolve_callback(payload)
    return {"ok": matched}


_mount_frontend_static()
