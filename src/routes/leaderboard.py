from fastapi import APIRouter, Depends
from src.state import leaderboard, show_qubits
from src.routes.admin import require_token

router = APIRouter()

@router.get("/leaderboard")
async def get_leaderboard():
    return leaderboard

@router.post("/show_qubits")
async def toggle_show_qubits(_: bool = Depends(require_token)):
    # toggle stored in state module
    from src.state import show_qubits as _show
    # update module-level variable
    new_val = not _show
    import src.state as s
    s.show_qubits = new_val
    return new_val

@router.get("/show_qubits")
async def get_show_qubits():
    from src.state import show_qubits as _show
    return _show

@router.delete("/reset")
async def reset(_: bool = Depends(require_token)):
    import src.state as s
    s.leaderboard = []
    return {"leaderboard": s.leaderboard}