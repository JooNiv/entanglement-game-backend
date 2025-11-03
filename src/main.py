from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import src.logging_setup

from src.routes import admin, submit, ws, device, leaderboard
from src import backend
from src.config import TRANSPILER_WORKERS
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# include routers
app.include_router(admin.router)
app.include_router(submit.router)
app.include_router(ws.router)
app.include_router(device.router)
app.include_router(leaderboard.router)

@app.on_event("startup")
async def start_workers():
    # initialize backend (lazy init also available)
    backend.init_backend()
    # start transpiler workers and batch worker
    from src.workers import transpiler, batcher
    for _ in range(TRANSPILER_WORKERS):
        asyncio.create_task(transpiler.transpile_worker())
    asyncio.create_task(batcher.batch_worker())
