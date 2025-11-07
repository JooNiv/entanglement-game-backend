from fastapi import APIRouter, WebSocket
import src.state as state

router = APIRouter()

@router.websocket("/ws/{task_id}")
async def ws_status(ws: WebSocket, task_id: str):
    await ws.accept()
    state.connected[task_id] = ws
    try:
        await ws.send_json({"status": "queued"})

        if task_id in state.pending_transpiled:
            msgs = state.pending_transpiled.pop(task_id)
            for m in msgs:
                try:
                    await ws.send_json(m)
                except Exception:
                    pass

        if task_id in state.pending_statuses:
            s_msgs = state.pending_statuses.pop(task_id)
            for s in s_msgs:
                try:
                    await ws.send_json(s)
                except Exception:
                    pass

        if task_id in state.pending_results:
            result = state.pending_results.pop(task_id)
            await ws.send_json({"status": "done", "result": result})
            await ws.close()
            return

        while True:
            try:
                await ws.receive_text()
            except Exception:
                break
    finally:
        if state.connected.get(task_id) is ws:
            state.connected.pop(task_id, None)