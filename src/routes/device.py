from fastapi import APIRouter, HTTPException, Depends
import os
import logging
from src.routes.admin import require_token
from src.backend import validate_qx_token, set_device as backend_set_device
import src.config as config
import src.state as state

router = APIRouter()

@router.post("/set_qx_token")
def set_qx_token(body: dict, _: bool = Depends(require_token)):
    qx_token = body.get("qx_token")
    if not qx_token:
        raise HTTPException(status_code=400, detail="Missing qx_token")
    valid = validate_qx_token(qx_token)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid Qx token")
    config.QX_TOKEN = qx_token
    os.environ["IQM_TOKEN"] = config.QX_TOKEN
    logging.info("Set QX token")
    return {"status": "success"}

@router.post("/reset_qx_token")
def reset_qx_token(_: bool = Depends(require_token)):
    config.QX_TOKEN = ""
    os.environ["IQM_TOKEN"] = ""
    logging.info("Reset QX token")
    return {"status": "success"}

@router.post("/set_project_id")
def set_project_id(body: dict, _: bool = Depends(require_token)):
    pid = body.get("project_id")
    config.PROJECT_ID = pid
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
    return {"device": config.DEVICE}

@router.post("/toggle_pause")
def toggle_pause(_: bool = Depends(require_token)):
    state.PAUSED = not state.PAUSED
    return {"paused": state.PAUSED}

@router.get("/get_project_id")
def get_project_id(_: bool = Depends(require_token)):
    return {"project_id": config.PROJECT_ID}