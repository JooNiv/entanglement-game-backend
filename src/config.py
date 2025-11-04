import os
from dotenv import load_dotenv

load_dotenv()

try:
    TEST = bool(int(os.getenv("TEST", "0")))
except Exception:
    TEST = False

QX_TOKEN = os.getenv("qx_token")
PROJECT_ID = os.getenv("slurm_project_id")
DEVICE = os.getenv("device") or "simulator"

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "secret")

BATCH_INTERVAL_SECONDS = int(os.getenv("BATCH_INTERVAL_SECONDS", "10"))
MAX_LEADERBOARD_SIZE = int(os.getenv("MAX_LEADERBOARD_SIZE", "100"))
TRANSPILER_WORKERS = int(os.getenv("TRANSPILER_WORKERS", "2"))
BATCH_MAX_CIRCUITS = int(os.getenv("BATCH_MAX_CIRCUITS", "100"))