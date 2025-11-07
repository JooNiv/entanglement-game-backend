from fastapi import APIRouter, Depends
import src.state as state
from src.routes.admin import require_token

router = APIRouter()

@router.get("/leaderboard")
async def get_leaderboard():
    return state.leaderboard

@router.post("/show_qubits")
async def toggle_show_qubits(_: bool = Depends(require_token)):
    # toggle stored in state module
    # update module-level variable
    new_val = not state.show_qubits
    state.show_qubits = new_val
    return new_val

@router.get("/show_qubits")
async def get_show_qubits():
    return state.show_qubits

@router.delete("/reset")
async def reset(_: bool = Depends(require_token)):
    state.leaderboard = []
    return {"leaderboard": state.leaderboard}