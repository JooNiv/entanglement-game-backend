from fastapi import FastAPI, Header, WebSocket, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import matplotlib
from qiskit import QuantumCircuit, transpile, QuantumRegister, ClassicalRegister
from qiskit.result import Result
from qiskit.result.models import ExperimentResult, ExperimentResultData, QobjExperimentHeader

from qiskit.circuit import CircuitInstruction
import asyncio
import uuid
from iqm.qiskit_iqm import IQMFakeAphrodite, IQMProvider

from qiskit.converters import circuit_to_dag
import logging
import io
import base64

from typing import Annotated

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import secrets, os, time
from dotenv import load_dotenv


show_qubits = False

load_dotenv()

try:
    TEST = bool(int(os.getenv("TEST")))
except Exception as e:
    logging.error(f"Error loading TEST environment variable: {e}")
    TEST = False
print(f"TEST mode: {TEST}")

PAUSED = False
QX_TOKEN = os.getenv("qx_token")
if QX_TOKEN:
    os.environ["IQM_TOKEN"] = QX_TOKEN

PROJECT_ID = os.getenv("slurm_project_id")
DEVICE = os.getenv("device") or "simulator"

backend = IQMFakeAphrodite()

try:
    if QX_TOKEN and DEVICE != "simulator" and not TEST:
        server_url = f"https://qx.vtt.fi/api/devices/{DEVICE}"
        provider = IQMProvider(server_url)
        backend = provider.get_backend()
    else:
        backend = IQMFakeAphrodite()
        DEVICE = "simulator"
        logging.warning(f"Using simulator backend: {DEVICE}")
except Exception as e:
    logging.error(f"Error connecting to IQM backend: {e}")
    backend = IQMFakeAphrodite()
    DEVICE = "simulator"


class RootOnlyFilter(logging.Filter):
    def filter(self, record):
        return record.name == "root"


handler = logging.StreamHandler()
handler.addFilter(RootOnlyFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[handler],
)

logging.getLogger().propagate = False

# Create a logger instance

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "secret")

# Store a temporary token in memory
TOKENS = {}

def get_valid_qubits(q1, q2):
    global DEVICE
    global backend

    if q1 <= 0 or q2 <= 0:
        return {"error": "Qubit indices must be positive integers"}

    max_qubit = max([int(i[2:]) for i in backend.architecture.qubits])

    if q1 > max_qubit or q2 > max_qubit:
        return {"error": f"Qubit indices must be less than {max_qubit}"}

    # q1 += 1
    # q2 += 1
    try:
        new_q_1 = backend._qb_to_idx["QB" + str(q1)]
    except Exception as _e:
        # print(f"Qubit {q1} is offline")
        return {"error": f"Qubit {q1} is offline"}
    try:
        new_q_2 = backend._qb_to_idx["QB" + str(q2)]
    except Exception as _e:
        # print(f"Qubit {q2} is offline")
        return {"error": f"Qubit {q2} is offline"}

    return new_q_1, new_q_2


def find_active_qubits(circuit):
    dag = circuit_to_dag(circuit)
    active_qubits = [circuit.find_bit(qubit).index for qubit in circuit.qubits if qubit not in dag.idle_wires()]

    return active_qubits


def remove_idle_qwires(circ):
    active_qubits = find_active_qubits(circ)

    qrs = []
    for i in active_qubits:
        qrs.append(QuantumRegister(1, i))

    cr = ClassicalRegister(2, "c")

    new_qc = QuantumCircuit(*qrs, cr)

    for i in circ.data:
        qubits = [active_qubits.index(circ.find_bit(j).index) for j in i.qubits]
        new_instruction = CircuitInstruction(i.operation, qubits, i.clbits)
        new_qc.append(new_instruction)

    return new_qc


connected = {}  # task_id -> websocket
task_queue = asyncio.Queue()

transpile_queue = asyncio.Queue()

# In-memory leaderboard list
leaderboard = []  # Each entry: {"username": str, "q1": int, "q2": int, "result": dict}

pending_results = {}
pending_transpiled = {}
pending_statuses = {}

transpiled_images = {}

# circuit_batch: a list of tasks awaiting batched execution
# Each entry: {"task_id": str, "username": str, "q1": int, "q2": int, "transpiled": QuantumCircuit}
circuit_batch = []

# Lock access to circuit_batch
batch_lock = asyncio.Lock()

# How often (seconds) to flush the batch and call backend.run on all collected circuits
BATCH_INTERVAL_SECONDS = 10
MAX_LEADERBOARD_SIZE = 100
BATCH_MAX_CIRCUITS = 100


async def transpile_circuit(task_id, username, q1, q2):
    pending_transpiled.setdefault(task_id, []).append({"status": "transpiling"})

    # Try to send an immediate 'transpiling' update if the websocket is
    # already connected
    ws = connected.get(task_id)
    if ws:
        try:
            await ws.send_json({"status": "transpiling"})
        except Exception as e:
            logging.info(f"Could not send 'transpiling' to {task_id}: {e}")
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])

    transpiled = transpile(qc, backend=backend, initial_layout=[q1, q2])
    new_transpiled = remove_idle_qwires(transpiled)
    logging.info(f"Transpiled circuit from task: {task_id}")

    msg = {"status": "transpiled"}

    try:
        fig = new_transpiled.draw(output="mpl")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        msg["image"] = f"data:image/png;base64,{img_b64}"
        transpiled_images[task_id] = msg["image"]

        logging.info(f"Rendered circuit image for task: {task_id}")
    except Exception as e:
        # if rendering fails, still send text
        logging.info(f"Error rendering circuit image for task {task_id}: {e}")
        msg["image_error"] = str(e)

    # Append the final transpiled payload (may include the rendered image).
    pending_transpiled.setdefault(task_id, []).append(msg)

    # If the client is already connected, try to send the final message now.
    ws = connected.get(task_id)
    if ws:
        try:
            await ws.send_json(msg)
        except Exception as e:
            logging.info(f"Could not send 'transpiled' to {task_id}: {e}")
    return transpiled


async def add_to_batch(task_id, username, q1, q2, transpiled):
    async with batch_lock:
        circuit_batch.append(
            {
                "task_id": task_id,
                "username": username,
                "q1": q1,
                "q2": q2,
                "transpiled": transpiled,
            }
        )

    logging.info(f"Added task {task_id} to batch (batch size now: {len(circuit_batch)})")


async def batch_worker():
    """Periodically flush the circuit_batch into sub-batches (max BATCH_MAX_CIRCUITS each)
    and execute each sub-batch with a separate backend.run call. Results are routed back
    to their originating task_id.
    """
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()

        # Grab and clear the full queue atomically
        async with batch_lock:
            if not circuit_batch:
                # Nothing to do this iteration, sleep full interval
                elapsed = loop.time() - start
                await asyncio.sleep(max(0, BATCH_INTERVAL_SECONDS - elapsed))
                continue
            all_items = list(circuit_batch)
            circuit_batch.clear()

        total = len(all_items)
        logging.info(f"Flushing {total} circuits from circuit_batch (max {BATCH_MAX_CIRCUITS} per run)")

        # Split into sub-batches of at most BATCH_MAX_CIRCUITS
        sub_batches = [all_items[i : i + BATCH_MAX_CIRCUITS] for i in range(0, total, BATCH_MAX_CIRCUITS)]

        # Process each sub-batch sequentially (one backend.run per sub-batch)
        for batch in sub_batches:
            batch_size = len(batch)
            logging.info(f"Submitting sub-batch of {batch_size} circuits to backend.run")

            # Mark each task as executing and try to notify connected websockets
            for t in batch:
                tid = t["task_id"]
                pending_statuses.setdefault(tid, []).append({"status": "executing"})
                ws = connected.get(tid)
                if ws:
                    try:
                        await ws.send_json({"status": "executing"})
                    except Exception as e:
                        logging.info(f"Could not send 'executing' to {tid}: {e}")

            # Prepare list of transpiled circuits
            circuits = [t["transpiled"] for t in batch]

            # Run the batch in an executor to not block event loop
            loop_inner = asyncio.get_running_loop()
            try:
                if TEST:
                    await asyncio.sleep(3)
                    
                    fake_res = Result(backend_name='aer_simulator', backend_version='0.13.3', qobj_id='', job_id='f370e58e-6e74-497b-b598-95145037111d', success=True, results=[ExperimentResult(shots=10, success=True, meas_level=2, data=ExperimentResultData(counts={'0x0': 400, '0x1':50, '0x2': 50, '0x3': 400}), header=QobjExperimentHeader(creg_sizes=[['c', 2]], global_phase=0.0, memory_slots=2, n_qubits=54, name='circuit-19120', qreg_sizes=[['control', 1], ['ancilla', 52], ['target', 1]], metadata={}), status="DONE", seed_simulator=2951211792, metadata={'time_taken': 0.022419774, 'num_bind_params': 1, 'parallel_state_update': 8, 'parallel_shots': 1, 'required_memory_mb': 1, 'input_qubit_map': [[1, 1], [0, 0]], 'method': 'density_matrix', 'device': 'CPU', 'num_qubits': 2, 'sample_measure_time': 0.000954547, 'active_input_qubits': [0, 1], 'num_clbits': 2, 'remapped_qubits': False, 'runtime_parameter_bind': False, 'max_memory_mb': 7644, 'noise': 'superop', 'measure_sampling': True, 'batched_shots_optimization': False, 'fusion': {'applied': False, 'max_fused_qubits': 2, 'threshold': 7, 'enabled': True}}, time_taken=0.022419774)], date="2025-10-30T11:26:54.745781", status="COMPLETED", header=None, metadata={'time_taken_parameter_binding': 0.000692017, 'time_taken_execute': 0.224359233, 'omp_enabled': True, 'max_gpu_memory_mb': 0, 'max_memory_mb': 7644, 'parallel_experiments': 1}, time_taken=6.077264070510864)

                    fake_res.results= len(circuits)*[fake_res.results[0]]
                    run_ret = fake_res
                else:
                    run_ret = await loop_inner.run_in_executor(None, lambda: backend.run(circuits, shots=1000).result())
            except Exception as e:
                logging.exception(f"Sub-batched run failed: {e}")
                # On failure, create empty results for all tasks in this sub-batch
                results_list = [{} for _ in range(batch_size)]
            else:
                # Normalize run_ret to a list of counts dicts in submission order.
                results_list = []
                try:
                    if hasattr(run_ret, "get_counts"):
                        counts = run_ret.get_counts()
                        if isinstance(counts, dict):
                            results_list = [counts]
                        else:
                            results_list = list(counts)
                    elif isinstance(run_ret, list):
                        for elem in run_ret:
                            if hasattr(elem, "get_counts"):
                                try:
                                    results_list.append(elem.get_counts())
                                except Exception:
                                    results_list.append({})
                            else:
                                results_list.append(elem)
                    elif isinstance(run_ret, dict):
                        results_list = [run_ret]
                    else:
                        if hasattr(run_ret, "results"):
                            for r in run_ret.results:
                                if hasattr(r, "get_counts"):
                                    try:
                                        results_list.append(r.get_counts())
                                    except Exception:
                                        results_list.append({})
                                else:
                                    results_list.append(r)
                        else:
                            results_list = [run_ret]
                except Exception as e:
                    logging.exception(f"Error normalizing sub-batched run result: {e}")
                    results_list = [{} for _ in range(batch_size)]

                # If the backend returned fewer results than expected, pad with empty dicts
                if len(results_list) < batch_size:
                    results_list.extend([{}] * (batch_size - len(results_list)))

            # Dispatch results back to tasks for this sub-batch
            for i, t in enumerate(batch):
                tid = t["task_id"]
                result = results_list[i] if i < len(results_list) else {}
                pending_results[tid] = result

                # Send done to connected websocket if present
                ws = connected.get(tid)
                if ws:
                    try:
                        await ws.send_json({"status": "done", "result": result})
                        await ws.close()
                    except Exception as e:
                        logging.info(f"Could not send 'done' to {tid}: {e}")

                # Update leaderboard
                leaderboard.append(
                    {
                        "username": t.get("username"),
                        "q1": backend._idx_to_qb[int(t.get("q1"))][2::],
                        "q2": backend._idx_to_qb[int(t.get("q2"))][2::],
                        "result": result,
                        "image": transpiled_images.get(tid),
                    }
                )

                if len(leaderboard) > MAX_LEADERBOARD_SIZE:
                    leaderboard.pop(0)

                transpiled_images.pop(tid, None)

            logging.info(f"Finished sub-batched run for {batch_size} circuits")

        # After processing all sub-batches, sleep only the remainder of the interval.
        elapsed = loop.time() - start
        sleep_time = max(0, BATCH_INTERVAL_SECONDS - elapsed)
        if sleep_time:
            await asyncio.sleep(sleep_time)


@app.on_event("startup")
async def start_worker():
    # Start the transpile worker and the batch runner
    asyncio.create_task(transpile_worker())
    asyncio.create_task(batch_worker())


async def transpile_worker():
    while True:
        task = await transpile_queue.get()

        transpiled = await transpile_circuit(**task)

        # Append slurm metadata if using a real device (not demo)
        # and project_id and QX_TOKEN are set
        if DEVICE != "demo" and PROJECT_ID and QX_TOKEN:
            if getattr(transpiled, "metadata", None) is None:
                transpiled.metadata = {}
            transpiled.metadata["project_id"] = PROJECT_ID

        task_id = task["task_id"]
        q1 = task["q1"]
        q2 = task["q2"]
        username = task["username"]

        # Add the transpiled circuit to the batch for periodic execution
        await add_to_batch(task_id, username, q1, q2, transpiled)
        transpile_queue.task_done()

def verify_admin(credentials: HTTPBasicCredentials):
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return True

@app.post("/admin/login")
def admin_login(credentials: HTTPBasicCredentials = Depends(security)):
    verify_admin(credentials)
    # Generate simple temporary token
    token = secrets.token_hex(16)
    TOKENS[token] = time.time() + 3600*4  # expires in 4 hours
    return {"token": token}

def require_token(x_token: str = Header(None)):
    if x_token not in TOKENS:
        raise HTTPException(status_code=401)
    if TOKENS[x_token] < time.time():
        raise HTTPException(status_code=401, detail="Token expired")
    return True

@app.post("/check_token")
def check_token(_: bool = Depends(require_token)):
    return {"status": "valid"}

@app.post("/submit")
async def submit(job: dict):

    if PAUSED:
        raise HTTPException(status_code=503, detail="Job submission is currently paused")

    q1 = job.get("q1")
    q2 = job.get("q2")

    if q1 is None or q2 is None:
        raise HTTPException(status_code=400, detail="Missing 'q1' or 'q2' in job submission")
    if q1 == q2:
        raise HTTPException(status_code=400, detail="'q1' and 'q2' must be different")
    valid_qubits = get_valid_qubits(q1, q2)
    if "error" in valid_qubits:
        raise HTTPException(status_code=400, detail=valid_qubits["error"])

    q1, q2 = valid_qubits

    job["q1"] = q1
    job["q2"] = q2

    task_id = str(uuid.uuid4())
    await transpile_queue.put({"task_id": task_id, **job})
    return {"task_id": task_id}


@app.websocket("/ws/{task_id}")
async def ws_status(ws: WebSocket, task_id: str):
    # Accept the websocket. After await ws.accept() the connection is established.
    await ws.accept()

    # Store the active websocket for this task id so background workers can send updates.
    connected[task_id] = ws

    try:
        # Tell the client the job is queued
        await ws.send_json({"status": "queued"})

        # If there are any pending transpile messages (e.g. 'transpiling', 'transpiled'),
        # send them all in order. Use pop to clear after sending.
        if task_id in pending_transpiled:
            msgs = pending_transpiled.pop(task_id)
            for m in msgs:
                try:
                    await ws.send_json(m)
                    logging.info(f"Sent pending transpile message for {task_id}")
                    # logging.info(f"Message content: {m}")
                except Exception as e:
                    logging.info(f"Could not send pending transpile message for {task_id}: {e}")

        # If there are any other pending status messages (e.g. 'executing'), send them too.
        if task_id in pending_statuses:
            s_msgs = pending_statuses.pop(task_id)
            for s in s_msgs:
                try:
                    await ws.send_json(s)
                except Exception as e:
                    logging.info(f"Could not send pending status message for {task_id}: {e}")

        # If the result is already ready, send it and close the connection.
        if task_id in pending_results:
            result = pending_results.pop(task_id)
            await ws.send_json({"status": "done", "result": result})
            await ws.close()
            return

        # Keep the connection open until the client disconnects
        while True:
            try:
                await ws.receive_text()
            except Exception:
                # client disconnected or error occurred — break out and cleanup
                break
    finally:
        if connected.get(task_id) is ws:
            connected.pop(task_id, None)


# Endpoint for leaderboard
@app.get("/leaderboard")
async def get_leaderboard():
    return leaderboard


# Global Qubit pair visibility toggle
@app.post("/show_qubits")
async def toggle_show_qubits(_: bool = Depends(require_token)):
    global show_qubits
    show_qubits = not show_qubits
    return show_qubits


@app.get("/show_qubits")
async def get_show_qubits():
    return show_qubits


@app.delete("/reset")
async def reset(_: bool = Depends(require_token)):
    global leaderboard
    leaderboard = []
    return {"leaderboard": leaderboard}

@app.post("/set_qx_token")
def set_qx_token(body: dict, _: bool = Depends(require_token)):
    global QX_TOKEN
    qx_token = body.get("qx_token")

    try:
        server_url = f"https://qx.vtt.fi/api/devices/demo"
        provider = IQMProvider(server_url)
        test_backend = provider.get_backend()
    except Exception as e:
        logging.error(f"Error validating IQM token: {e}")
        raise HTTPException(status_code=400, detail="Invalid Qx token") 

    QX_TOKEN = qx_token
    os.environ["IQM_TOKEN"] = QX_TOKEN
    logging.info(QX_TOKEN)
    return {"status": "success"}

@app.post("/set_project_id")
def set_project_id(body: dict, _: bool = Depends(require_token)):
    global PROJECT_ID
    PROJECT_ID = body.get("project_id")
    logging.info(PROJECT_ID)
    return {"status": "success"}

@app.post("/set_device")
def set_device(body: dict, _: bool = Depends(require_token)):
    global DEVICE
    global backend
    device = body.get("device")

    prev_device = DEVICE
    
    if device == prev_device:
        return {"device": DEVICE}
    elif device == "simulator":
        backend = IQMFakeAphrodite()
        return {"device": device}
    else:
        try:
            if QX_TOKEN and device != "simulator" and not TEST:
                server_url = f"https://qx.vtt.fi/api/devices/{device}"
                provider = IQMProvider(server_url)
                backend = provider.get_backend()
            else:
                backend = IQMFakeAphrodite()
                DEVICE = "simulator"
                return {"device": DEVICE, "error": "No valid Qx token, using simulator."}
        except Exception as e:
            logging.error(f"Error connecting to IQM backend: {e}")
            server_url = f"https://qx.vtt.fi/api/devices/{prev_device}"
            provider = IQMProvider(server_url)
            backend = provider.get_backend()
            return {"device": prev_device, "error": "Could not connect to device, reverted to previous."}

    DEVICE = device
    logging.info(DEVICE)
    return {"device": DEVICE}

@app.get("/get_device")
def get_device():
    return {"device": DEVICE}

@app.post("/toggle_pause")
def toggle_pause(_: bool = Depends(require_token)):
    global PAUSED
    PAUSED = not PAUSED
    return {"paused": PAUSED}