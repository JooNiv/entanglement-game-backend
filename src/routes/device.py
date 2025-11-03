from fastapi import APIRouter, HTTPException, Depends
import os, logging
from src.routes.admin import require_token
from src.backend import validate_qx_token, set_device as backend_set_device
from src.config import PROJECT_ID, QX_TOKEN, DEVICE

router = APIRouter()

@router.post("/set_qx_token")
def set_qx_token(body: dict, _: bool = Depends(require_token)):
    global QX_TOKEN
    qx_token = body.get("qx_token")
    if not qx_token:
        raise HTTPException(status_code=400, detail="Missing qx_token")
    valid = validate_qx_token(qx_token)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid Qx token")
    QX_TOKEN = qx_token
    os.environ["IQM_TOKEN"] = QX_TOKEN
    logging.info("Set QX token")
    return {"status": "success"}

@router.post("/set_project_id")
def set_project_id(body: dict, _: bool = Depends(require_token)):
    global PROJECT_ID
    logging.info(PROJECT_ID)
    pid = body.get("project_id")
    # project id stored in config module; updating env so backend init can pick it up
    PROJECT_ID = pid
    logging.info(f"Set project id: {pid}")
    return {"status": "success"}

@router.post("/set_device")
def set_device(body: dict, _: bool = Depends(require_token)):
    device = body.get("device")
    if not device:
        raise HTTPException(status_code=400, detail="Missing device")
    res = backend_set_device(device)
    return res

@router.get("/get_device")
def get_device():
    global DEVICE
    return {"device": DEVICE}