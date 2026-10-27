"""Owner-session-only SilverBullet read-only sidecar transport.

The upstream is fixed to loopback: no URL or hostname comes from a request.
The SilverBullet service has SB_READ_ONLY and no browser/offline sync. This
proxy exposes only bounded reads/queries; it never forwards write verbs.
"""
from __future__ import annotations

import asyncio
from urllib.parse import unquote

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect

from backend.auth import admit_websocket
from backend.fleet2.workspace_common import EXCLUDED_NAMES

router = APIRouter()
_UPSTREAM = "http://127.0.0.1:3082"
_MAX_QUERY_BYTES = 256_000
_SEARCH_POST_PATHS = frozenset({".search/query", ".search/related"})
_BLOCKED_ENDPOINTS = frozenset({
    ".shell", ".proxy", ".runtime", ".dashboard", ".revisions", ".auth", ".logout",
})


def _excluded_file(path: str) -> bool:
    """Do not expose hidden or excluded files even through the sidecar's FS API."""
    for candidate in (path, unquote(path), unquote(unquote(path))):
        if not candidate.startswith(".fs/"):
            continue
        segments = candidate.removeprefix(".fs/").split("/")
        if any(part in {"", ".", ".."} or part.startswith(".") or part in EXCLUDED_NAMES
               for part in segments):
            return True
    return False


@router.api_route("/notes", methods=["GET", "HEAD", "POST"])
@router.api_route("/notes/{path:path}", methods=["GET", "HEAD", "POST"])
async def silverbullet_read(request: Request, path: str = "") -> Response:
    if path.split("/", 1)[0] in _BLOCKED_ENDPOINTS:
        raise HTTPException(405 if request.method == "POST" else 404, "SilverBullet endpoint unavailable")
    if _excluded_file(path):
        raise HTTPException(404, "SilverBullet file excluded")
    if request.method == "POST":
        if path not in _SEARCH_POST_PATHS:
            raise HTTPException(405, "SilverBullet only permits read-only search queries")
        try:
            declared_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid SilverBullet query length") from None
        if declared_length > _MAX_QUERY_BYTES:
            raise HTTPException(413, "SilverBullet query exceeds limit")
        data = await request.body()
        if len(data) > _MAX_QUERY_BYTES:
            raise HTTPException(413, "SilverBullet query exceeds limit")
    else:
        data = b""
    headers = {key: value for key, value in request.headers.items()
               if key.lower() in {
                   "accept", "content-type", "if-none-match", "if-modified-since", "range",
                   "x-sync-mode", "x-get-meta",
               }}
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
            upstream = await client.request(
                request.method, _UPSTREAM + request.url.path,
                params=request.query_params, content=data, headers=headers,
            )
    except (httpx.RequestError, httpx.TimeoutException):
        raise HTTPException(503, "SilverBullet read-only sidecar unavailable") from None
    response_headers = {"Cache-Control": "no-store"}
    for key in (
        "content-type", "etag", "last-modified", "location", "content-range",
        "x-permission", "x-last-modified", "x-created", "x-content-length",
    ):
        if key in upstream.headers:
            response_headers[key] = upstream.headers[key]
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


@router.websocket("/notes/{path:path}")
async def silverbullet_websocket(websocket: WebSocket, path: str) -> None:
    if not await admit_websocket(websocket, websocket.app.state.session_store):
        return
    suffix = ("?" + websocket.url.query) if websocket.url.query else ""
    try:
        async with websockets.connect(
            "ws://127.0.0.1:3082" + websocket.url.path + suffix,
            open_timeout=5, max_size=256_000,
        ) as upstream:
            await websocket.accept()

            async def to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def to_browser() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            tasks = {asyncio.create_task(to_upstream()), asyncio.create_task(to_browser())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, websockets.WebSocketException, WebSocketDisconnect):
        await websocket.close(code=1013)
