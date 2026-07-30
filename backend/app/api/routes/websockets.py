from __future__ import annotations

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.routes.common import load_run
from app.core.database import SessionLocal
from app.core.logging import user_id_ctx
from app.core.security import decode_token
from app.models import User
from app.services.events import broker
from app.services.terminal import handle_order_workflow_terminal, handle_resource_terminal

router = APIRouter()

@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: int, token: str = Query(...)) -> None:
    try: payload = decode_token(token, "access")
    except jwt.InvalidTokenError: await websocket.close(code=4401); return
    db = SessionLocal()
    try:
        user = db.get(User, int(payload["sub"])); run = load_run(db, run_id)
        if not user or not user.is_active: await websocket.close(code=4401); return
        user_id_ctx.set(user.id)
        websocket.state.observability_user_id = user.id
        await websocket.accept(); await websocket.send_json({"type": "snapshot", "status": run.status, "progress": run.progress})
        queue = await broker.subscribe(run_id)
        try:
            while True: await websocket.send_json(await queue.get())
        except WebSocketDisconnect: pass
        finally: broker.unsubscribe(run_id, queue)
    finally: db.close()


@router.websocket("/ws/resources/{resource_id}/terminal")
async def resource_terminal(websocket: WebSocket, resource_id: int, token: str = Query(...)) -> None:
    await handle_resource_terminal(websocket, resource_id, token)


@router.websocket("/ws/runs/{run_id}/steps/{step_id}/order-terminal")
async def order_workflow_terminal(websocket: WebSocket, run_id: int, step_id: int, token: str = Query(...)) -> None:
    await handle_order_workflow_terminal(websocket, run_id, step_id, token)
