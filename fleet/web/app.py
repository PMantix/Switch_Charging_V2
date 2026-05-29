"""
Fleet web dashboard — FastAPI + WebSocket for real-time fleet monitoring.

Serves a single-page dashboard that shows all Pis in a grid with live
state updates pushed over WebSocket. REST endpoints allow queuing
commands to any Pi.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fleet.state_store import FleetStateStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(store: FleetStateStore) -> FastAPI:
    app = FastAPI(title="Switch Charging Fleet Dashboard")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    ws_clients: list[WebSocket] = []

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/fleet")
    async def get_fleet():
        return JSONResponse({"pis": store.get_all()})

    @app.get("/api/pi/{pi_num}")
    async def get_pi(pi_num: int):
        data = store.get_pi(pi_num)
        if data is None:
            return JSONResponse({"error": "unknown pi"}, status_code=404)
        return JSONResponse(data)

    @app.post("/api/pi/{pi_num}/command")
    async def send_command(pi_num: int, body: dict):
        cmd = body.get("cmd")
        if not cmd:
            return JSONResponse({"error": "missing cmd field"}, status_code=400)
        cmd_id = store.enqueue_command(pi_num, body)
        return JSONResponse({"ok": True, "cmd_id": cmd_id})

    @app.get("/api/commands")
    async def get_commands(pi_num: Optional[int] = None):
        return JSONResponse({"commands": store.get_commands(pi_num=pi_num)})

    @app.post("/api/refresh")
    async def refresh_all():
        cyc = getattr(app.state, "cycler", None)
        if cyc is None:
            return JSONResponse(
                {"ok": False, "error": "cycler not running (dashboard-only mode)"},
                status_code=409,
            )
        cyc.request_repoll()
        return JSONResponse({"ok": True, "repoll": "all"})

    @app.post("/api/pi/{pi_num}/refresh")
    async def refresh_pi(pi_num: int):
        cyc = getattr(app.state, "cycler", None)
        if cyc is None:
            return JSONResponse(
                {"ok": False, "error": "cycler not running (dashboard-only mode)"},
                status_code=409,
            )
        cyc.request_repoll(pi_num)
        return JSONResponse({"ok": True, "repoll": pi_num})

    @app.get("/api/cycler")
    async def get_cycler_status():
        # Injected by __main__ after cycler is created
        info = getattr(app.state, "cycler_info", None)
        if info:
            return JSONResponse(info())
        return JSONResponse({"status": "unknown"})

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        ws_clients.append(ws)
        log.info("WebSocket client connected (%d total)", len(ws_clients))
        try:
            # Send initial state
            await ws.send_json({"type": "fleet", "pis": store.get_all()})
            # Keep alive and send updates
            while True:
                await asyncio.sleep(1.0)
                try:
                    await ws.send_json({"type": "fleet", "pis": store.get_all()})
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if ws in ws_clients:
                ws_clients.remove(ws)
            log.info("WebSocket client disconnected (%d remaining)", len(ws_clients))

    return app
