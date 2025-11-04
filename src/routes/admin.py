from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import time
import src.state as state
from src.config import ADMIN_USER, ADMIN_PASS

router = APIRouter(prefix="/admin")
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials):
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return True

@router.post("/login")
def admin_login(credentials: HTTPBasicCredentials = Depends(security)):
    verify_admin(credentials)
    token = secrets.token_hex(16)
    state.TOKENS[token] = time.time() + 3600 * 4  # expires in 4 hours
    return {"token": token}

def require_token(x_token: str = Header(None)):
    if x_token is None:
        raise HTTPException(status_code=401)
    if x_token not in state.TOKENS:
        raise HTTPException(status_code=401)
    if state.TOKENS[x_token] < time.time():
        raise HTTPException(status_code=401, detail="Token expired")
    return True

@router.post("/check_token")
def check_token(_: bool = Depends(require_token)):
    return {"status": "valid"}