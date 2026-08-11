"""minepaint web server (FastAPI on port 8766).

- GET /   -> serves web/viewer.html (Three.js viewer)
- WS  /ws -> pushes full world JSON to every connected viewer on each mutation
- /mcp    -> the FastMCP server mounted over HTTP (streamable-http), so remote
             MCP clients can drive the same world the browser shows

Run:  python -m minepaint.web_server
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketState

from minepaint.mcp_server import add_mutation_listener, mcp, world

logger = logging.getLogger("minepaint.web")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# FastMCP's HTTP (streamable-http) app. Mounted at "/" AFTER all app routes
# below (see bottom of file) with internal path "/mcp", so /mcp hits it
# directly - no redirect, no path-strip issues. Its lifespan must be passed
# to the parent FastAPI app or the session manager task group never starts.
mcp_app = mcp.http_app(path="/mcp")
app = FastAPI(title="minepaint", version="1.0.0", lifespan=mcp_app.lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB_DIR / "viewer.html").read_text(encoding="utf-8")


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        data = json.dumps(payload)
        for ws in list(self.connections):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(data)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        # Immediately hand the current state to a freshly-connected viewer.
        await ws.send_text(json.dumps(world.to_json()))
        while True:
            # Viewers are read-only; we only need to keep the socket alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


_main_loop: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _capture_loop() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def _on_mutation() -> None:
    """Called synchronously (possibly from a worker thread) after each mutation."""
    loop = _main_loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(manager.broadcast(world.to_json()))
        )
    except RuntimeError:
        pass  # loop already closed; nothing to notify


add_mutation_listener(_on_mutation)

# Mount AFTER all routes so / and /ws win; everything else (i.e. /mcp and
# /mcp/...) falls through to the FastMCP streamable-http app.
app.mount("/", mcp_app, name="minepaint-mcp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8766, log_level="info")
