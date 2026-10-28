"""Signal Deck v3 application entrypoint.

Keeps the proven runtime spine - Tailscale Serve proxy boundary, owner session,
CSRF, systemd user unit - and replaces the product surface above it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.agents_api import router as agents_router  # noqa: E402
from backend.agents_hermes import router as hermes_router  # noqa: E402
from backend.agents_live import router as agents_live_router  # noqa: E402
from backend.codex_sessions import router as codex_sessions_router  # noqa: E402
from backend.desktops import router as desktops_router  # noqa: E402
from backend.workspace_silverbullet import router as silverbullet_router  # noqa: E402
from backend.auth import SessionStore, TailnetAuthMiddleware  # noqa: E402
from backend.fleet2.actions import ActionEngine  # noqa: E402
from backend.fleet2.api import build_router  # noqa: E402
from backend.fleet2.workspace_obsidian_routes import router as obsidian_note_router  # noqa: E402
from backend.fleet2.poller import FleetPoller  # noqa: E402
from backend.fleet2.registry import load_registry  # noqa: E402
from backend.fleet2.store import Store  # noqa: E402
from backend.omniroute_status import router as omniroute_router  # noqa: E402
from backend.runtime_config import get_runtime_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("signaldeck")

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = REPO_ROOT.parent / "web" / "dist"
TOKEN_ENV = "FLEETPROBE_TOKEN"
TOKEN_FILE_ENV = "FLEETPROBE_TOKEN_FILE"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "controlcenter" / "secrets" / "fleetprobe-token"

# Only these extensions are ever served from the built SPA directory.
_ALLOWED_ASSET_SUFFIXES = {".js", ".css", ".map", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff2", ".ico", ".json"}
_MEDIA_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
    ".json": "application/json",
}


def _poller_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    candidate = Path(os.environ.get(TOKEN_FILE_ENV, "")) if os.environ.get(TOKEN_FILE_ENV) else DEFAULT_TOKEN_FILE
    if candidate.is_file():
        token = candidate.read_text(encoding="utf-8").strip()
        if token:
            return token
        raise RuntimeError(f"agent token file is empty: {candidate}")
    raise RuntimeError(
        f"no agent token: set {TOKEN_ENV} or place it in {DEFAULT_TOKEN_FILE}"
    )


def create_app() -> FastAPI:
    registry = load_registry()
    runtime = get_runtime_config()
    runtime.ensure_directories()
    store = Store(
        runtime.database_path,
        sample_retention_days=runtime.sample_retention_days,
        receipt_retention_days=runtime.receipt_retention_days,
    )
    poller = FleetPoller(registry, store, _poller_token())
    engine = ActionEngine(registry, store)
    session_store = SessionStore()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        interrupted = await asyncio.to_thread(store.interrupt_abandoned_server_receipts)
        if interrupted:
            logger.warning("reconciled %d abandoned running receipts", interrupted)
        poller.start()
        logger.info("poller started for %d hosts", len(registry.hosts))
        try:
            yield
        finally:
            await poller.stop()

    dev_mode = os.getenv("SIGNALDECK_DEV") == "1"
    app = FastAPI(
        title="Signal Deck",
        version="3.0.0",
        docs_url="/docs" if dev_mode else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev_mode else None,
        lifespan=lifespan,
    )
    app.add_middleware(TailnetAuthMiddleware, store=session_store)
    app.include_router(agents_router)
    app.include_router(hermes_router)
    app.include_router(agents_live_router)
    app.include_router(codex_sessions_router)
    app.include_router(desktops_router)
    app.include_router(silverbullet_router)
    app.include_router(omniroute_router)
    app.include_router(obsidian_note_router)
    app.include_router(
        build_router(
            registry=registry,
            poller=poller,
            store=store,
            engine=engine,
            session_store=session_store,
        )
    )

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    def index() -> Response:
        html = WEB_DIST / "index.html"
        if not html.is_file():
            return HTMLResponse(
                "<h1>Signal Deck</h1><p>The web bundle is missing. Run <code>npm run build</code> in <code>web/</code>.</p>",
                status_code=503,
            )
        return HTMLResponse(
            html.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/workspace", include_in_schema=False)
    def workspace_page() -> RedirectResponse:
        """Owner-only SPA deep link to the existing workspace view."""
        return RedirectResponse(url="/#workspace", status_code=307)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def pwa_manifest() -> FileResponse:
        return FileResponse(WEB_DIST / "manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-store"})

    @app.get("/sw.js", include_in_schema=False)
    def pwa_service_worker() -> FileResponse:
        """Serve the authenticated PWA worker at the origin root for scope '/'."""
        target = WEB_DIST / "sw.js"
        if not target.is_file():
            raise HTTPException(status_code=503, detail="service worker missing from release")
        return FileResponse(
            target,
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store",
                "Service-Worker-Allowed": "/",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/plancia-icon.svg", include_in_schema=False)
    def pwa_icon() -> FileResponse:
        return FileResponse(WEB_DIST / "plancia-icon.svg", media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/session")
    def session(request: Request) -> dict:
        """Explicit bootstrap/inspection endpoint for the short-lived owner session.

        The auth middleware creates a new secure cookie pair on this allowlisted
        route whenever the previous in-memory session is missing or expired.
        Returning only bounded session metadata avoids exposing the owner login.
        """
        owner_session = request.state.owner_session
        return {
            "authenticated": True,
            "role": request.state.principal.role,
            "expires_at": owner_session.expires_at,
            "api_version": app.version,
        }

    @app.get("/assets/{asset_path:path}")
    def asset(asset_path: str) -> Response:
        target = (WEB_DIST / "assets" / asset_path).resolve()
        assets_root = (WEB_DIST / "assets").resolve()
        if assets_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        if target.suffix not in _ALLOWED_ASSET_SUFFIXES:
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(
            target,
            media_type=_MEDIA_TYPES.get(target.suffix, "application/octet-stream"),
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # Exposed for tests and for operational inspection; not a route.
    app.state.session_store = session_store
    app.state.poller = poller
    app.state.store = store
    app.state.registry = registry

    @app.get("/healthz")
    def healthz() -> dict:
        snapshot = poller.snapshot()
        return {
            "ok": True,
            "hosts": snapshot["fleet"]["total"],
            "reachable": snapshot["fleet"]["reachable"],
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="127.0.0.1", port=8000)
