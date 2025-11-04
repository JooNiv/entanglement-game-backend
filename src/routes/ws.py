from fastapi import APIRouter, WebSocket
from src.state import connected, pending_transpiled, pending_statuses, pending_results

router = APIRouter()

@router.websocket("/ws/{task_id}")
async def ws_status(ws: WebSocket, task_id: str):
    await ws.accept()
    connected[task_id] = ws
    try:
        await ws.send_json({"status": "queued"})

        if task_id in pending_transpiled:
            msgs = pending_transpiled.pop(task_id)
            for m in msgs:
                try:
                    await ws.send_json(m)
                except Exception:
                    pass

        if task_id in pending_statuses:
            s_msgs = pending_statuses.pop(task_id)
            for s in s_msgs:
                try:
                    await ws.send_json(s)
                except Exception:
                    pass

        if task_id in pending_results:
            result = pending_results.pop(task_id)
            await ws.send_json({"status": "done", "result": result})
            await ws.close()
            return

        while True:
            try:
                await ws.receive_text()
            except Exception:
                break
    finally:
        if connected.get(task_id) is ws:
            connected.pop(task_id, None)