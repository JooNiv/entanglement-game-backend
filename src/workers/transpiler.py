import asyncio
import base64
import io
import logging
from qiskit import QuantumCircuit, transpile
import src.state as state
import src.backend as backend_module
import src.config as config
from src.utils import remove_idle_qwires
import matplotlib.pyplot as plt

async def add_to_batch(task_id, username, q1, q2, transpiled):
    async with state.batch_lock:
        state.circuit_batch.append(
            {
                "task_id": task_id,
                "username": username,
                "q1": q1,
                "q2": q2,
                "transpiled": transpiled,
            }
        )

    logging.info(f"Added task {task_id} to batch (batch size now: {len(state.circuit_batch)})")


async def transpile_circuit(task_id, username, q1, q2):
    state.pending_transpiled.setdefault(task_id, []).append({"status": "transpiling"})

    # Try to send an immediate 'transpiling' update if the websocket is
    # already connected
    ws = state.connected.get(task_id)
    if ws:
        try:
            await ws.send_json({"status": "transpiling"})
        except Exception as e:
            logging.info(f"Could not send 'transpiling' to {task_id}: {e}")

    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])

    # Offload the blocking transpile call to the default threadpool
    loop = asyncio.get_running_loop()
    try:
        transpiled = await loop.run_in_executor(None, lambda: transpile(qc, backend=backend_module.backend, initial_layout=[q1, q2]))
        # remove_idle_qwires is cheap; can run in-event-loop
        new_transpiled = remove_idle_qwires(transpiled)
    except Exception as e:
        logging.exception(f"Transpile failed for task {task_id}: {e}")
        raise

    logging.info(f"Transpiled circuit from task: {task_id}")

    msg = {"status": "transpiled"}

    try:
        # Rendering can also be blocking, so run in executor.
        def render_image(circuit):
            fig = circuit.draw(output="mpl")
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            return base64.b64encode(buf.getvalue()).decode("ascii")

        img_b64 = await loop.run_in_executor(None, lambda: render_image(new_transpiled))
        msg["image"] = f"data:image/png;base64,{img_b64}"
        state.transpiled_images[task_id] = msg["image"]

        logging.info(f"Rendered circuit image for task: {task_id}")
    except Exception as e:
        logging.info(f"Error rendering circuit image for task {task_id}: {e}")
        msg["image_error"] = str(e)

    # Append the final transpiled payload (may include the rendered image).
    state.pending_transpiled.setdefault(task_id, []).append(msg)

    # If the client is already connected, try to send the final message now.
    ws = state.connected.get(task_id)
    if ws:
        try:
            await ws.send_json(msg)
        except Exception as e:
            logging.info(f"Could not send 'transpiled' to {task_id}: {e}")
    return transpiled

async def transpile_worker():
    while True:
        task = await state.transpile_queue.get()

        transpiled = await transpile_circuit(**task)

        # Append slurm metadata if using a real device (not demo)
        # and project_id and QX_TOKEN are set
        if config.DEVICE != "demo" and config.PROJECT_ID and config.QX_TOKEN:
            if getattr(transpiled, "metadata", None) is None:
                transpiled.metadata = {}
            transpiled.metadata["project_id"] = config.PROJECT_ID

        logging.info(f"Added project_id metadata to transpiled circuit for task {task['task_id']}")
        logging.info(f"Transpiled circuit metadata: {transpiled.metadata}")

        task_id = task["task_id"]
        q1 = task["q1"]
        q2 = task["q2"]
        username = task["username"]

        # Add the transpiled circuit to the batch for periodic execution
        await add_to_batch(task_id, username, q1, q2, transpiled)
        state.transpile_queue.task_done()