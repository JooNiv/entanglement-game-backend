from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import matplotlib
from qiskit import QuantumCircuit, transpile, QuantumRegister, ClassicalRegister
from qiskit.circuit import CircuitInstruction
import asyncio
import uuid
from iqm.qiskit_iqm import IQMFakeAphrodite, IQMProvider

from qiskit.converters import circuit_to_dag
import logging
import io
import base64
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import os
from dotenv import load_dotenv

show_qubits = False

load_dotenv()

qx_token = os.getenv('qx_token')
if qx_token:
    os.environ["IQM_TOKEN"] = qx_token

project_id = os.getenv('slurm_project_id')
device = os.getenv('device')

backend = IQMFakeAphrodite()

if  qx_token:
    server_url = f"https://qx.vtt.fi/api/devices/{device}"
    provider = IQMProvider(server_url)
    backend = provider.get_backend()

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

def find_active_qubits(circuit):

   dag = circuit_to_dag(circuit)
   active_qubits = [circuit.find_bit(qubit).index for qubit in circuit.qubits 
                    if qubit not in dag.idle_wires()]

   return active_qubits

def remove_idle_qwires(circ):
    active_qubits = find_active_qubits(circ)

    qrs = []
    for i in active_qubits:
        qrs.append(QuantumRegister(1, i))

    cr = ClassicalRegister(2, 'c')

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
MAX_LEADERBOARD_SIZE = 200

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
        fig = new_transpiled.draw(output='mpl')
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
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

async def run_simulation(task_id, username, q1, q2, transpiled):
    pending_statuses.setdefault(task_id, []).append({"status": "executing"})

    ws = connected.get(task_id)
    if ws:
        try:
            await ws.send_json({"status": "executing"})
        except Exception as e:
            logging.info(f"Could not send 'executing' to {task_id}: {e}")
    logging.info(f"Here, starting run: {task_id}")
    
    # run in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: backend.run(transpiled, shots=1000).result().get_counts()
        )
    except Exception as e:
        logging.exception(f"Run failed for task {task_id}: {e}")
        result = {}
    pending_results[task_id] = result

    
    logging.info(f"Finished circuit from task: {task_id}")
    if ws:
        try:
            await ws.send_json({"status": "done", "result": result})
            await ws.close()
        except Exception as e:
            logging.info(f"Could not send 'done' to {task_id}: {e}")

    # Add to leaderboard
    leaderboard.append(
        {
            "username": username,
            "q1": q1,
            "q2": q2,
            "result": result,
            "image": transpiled_images.get(task_id)
        }
    )

    if len(leaderboard) > MAX_LEADERBOARD_SIZE:
        leaderboard.pop(0)

    transpiled_images.pop(task_id, None)

    # Notify user
    ws = connected.get(task_id)
    if ws:
        try:
            await ws.send_json({"status": "done", "result": result})
            await ws.close()
        except Exception as e:
            logging.info(f"Could not resend 'done' to {task_id}: {e}")

async def add_to_batch(task_id, username, q1, q2, transpiled):
    async with batch_lock:
        circuit_batch.append({
            "task_id": task_id,
            "username": username,
            "q1": q1,
            "q2": q2,
            "transpiled": transpiled,
        })

    logging.info(f"Added task {task_id} to batch (batch size now: {len(circuit_batch)})")


async def batch_worker():
    """Periodically flush the circuit batch and execute all collected transpiled circuits
    in one backend.run call. Each result is routed back to its originating task_id.
    """
    while True:
        await asyncio.sleep(BATCH_INTERVAL_SECONDS)

        # Grab and clear the batch atomically
        async with batch_lock:
            if not circuit_batch:
                continue
            batch = list(circuit_batch)
            circuit_batch.clear()

        batch_size = len(batch)
        logging.info(f"Flushing batch of {batch_size} circuits to backend.run")

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
        loop = asyncio.get_running_loop()
        try:
            run_ret = await loop.run_in_executor(
                None, lambda: backend.run(circuits, shots=1000).result()
            )
        except Exception as e:
            logging.exception(f"Batched run failed: {e}")
            # On failure, create empty results for all tasks
            results_list = [{} for _ in range(batch_size)]
        else:
            # Normalize run_ret to a list of counts dicts in submission order.
            results_list = []
            try:
                # If run_ret has a get_counts method, use it.
                if hasattr(run_ret, "get_counts"):
                    counts = run_ret.get_counts()
                    # If single dict, wrap it
                    if isinstance(counts, dict):
                        results_list = [counts]
                    else:
                        # If get_counts returned list-like
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
                    # Try to .results
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
                        # Fallback: treat as single result if possible
                        results_list = [run_ret]
            except Exception as e:
                logging.exception(f"Error normalizing batched run result: {e}")
                results_list = [{} for _ in range(batch_size)]

            # If the backend returned fewer results than expected, pad with empty dicts
            if len(results_list) < batch_size:
                results_list.extend([{}] * (batch_size - len(results_list)))

        # Dispatch results back to tasks
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
                    "q1": t.get("q1"),
                    "q2": t.get("q2"),
                    "result": result,
                    "image": transpiled_images.get(tid),
                }
            )

            if len(leaderboard) > 200:
                leaderboard.pop(0)

            transpiled_images.pop(tid, None)

        logging.info(f"Finished batched run for {batch_size} circuits")

@app.on_event("startup")
async def start_worker():
    # Start the transpile worker and the batch runner
    asyncio.create_task(transpile_worker())
    asyncio.create_task(batch_worker())


async def worker():
    while True:
        task = await task_queue.get()
        # await add_to_batch(**task)
        await run_simulation(**task)
        task_queue.task_done()


async def transpile_worker():
    while True:
        task = await transpile_queue.get()

        transpiled = await transpile_circuit(**task)
        
        # Append slurm metadata if using a real device (not demo)
        # and project_id and qx_token are set
        if device != "demo" and project_id and qx_token:
            if getattr(transpiled, "metadata", None) is None:
                transpiled.metadata = {}
            transpiled.metadata["project_id"] = project_id

        task_id = task["task_id"]
        q1 = task["q1"]
        q2 = task["q2"]
        username = task["username"]

        # Add the transpiled circuit to the batch for periodic execution
        await add_to_batch(task_id, username, q1, q2, transpiled)
        transpile_queue.task_done()


@app.post("/submit")
async def submit(job: dict):
    q1 = job.get("q1")
    q2 = job.get("q2")

    if q1 is None or q2 is None:
        raise HTTPException(status_code=400, detail="Missing 'q1' or 'q2' in job submission")
    if q1 == q2:
        raise HTTPException(status_code=400, detail="'q1' and 'q2' must be different (cannot target the same qubit twice)")

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
                    #logging.info(f"Message content: {m}")
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
async def toggle_show_qubits():
    global show_qubits
    show_qubits = not show_qubits
    return show_qubits

@app.get("/show_qubits")
async def get_show_qubits():
    return show_qubits