import asyncio

# runtime shared state
connected = {}               # task_id -> websocket
transpile_queue = asyncio.Queue()
task_queue = asyncio.Queue()

pending_results = {}
pending_transpiled = {}
pending_statuses = {}
transpiled_images = {}

circuit_batch = []
batch_lock = asyncio.Lock()

leaderboard = []

PAUSED = False
TOKENS = {}
show_qubits = False
