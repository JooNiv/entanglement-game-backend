from fastapi import APIRouter, HTTPException
import uuid
import src.state as state
from src.backend import map_user_qubits, QubitMappingError

router = APIRouter()

@router.post("/submit")
async def submit(job: dict):
    if state.PAUSED:
        raise HTTPException(status_code=503, detail="Job submission is currently paused")

    q1 = job.get("q1")
    q2 = job.get("q2")

    if q1 is None or q2 is None:
        raise HTTPException(status_code=400, detail="Missing 'q1' or 'q2' in job submission")
    if q1 == q2:
        raise HTTPException(status_code=400, detail="'q1' and 'q2' must be different")

    try:
        mapped_q1, mapped_q2 = map_user_qubits(int(q1), int(q2))
    except QubitMappingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job["q1"] = mapped_q1
    job["q2"] = mapped_q2

    task_id = str(uuid.uuid4())
    await state.transpile_queue.put({"task_id": task_id, **job})
    return {"task_id": task_id}