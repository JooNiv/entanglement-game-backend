import asyncio
import logging
import src.state as state
from src.config import TEST, BATCH_INTERVAL_SECONDS, BATCH_MAX_CIRCUITS, MAX_LEADERBOARD_SIZE
import src.backend as backend_module


async def batch_worker():
    """Periodically flush the circuit_batch into sub-batches (max BATCH_MAX_CIRCUITS each)
    and execute each sub-batch with a separate backend.run call. Results are routed back
    to their originating task_id.
    """
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()

        # Grab and clear the full queue atomically
        async with state.batch_lock:
            if not state.circuit_batch:
                # Nothing to do this iteration, sleep full interval
                elapsed = loop.time() - start
                await asyncio.sleep(max(0, BATCH_INTERVAL_SECONDS - elapsed))
                continue
            all_items = list(state.circuit_batch)
            state.circuit_batch.clear()

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
                state.pending_statuses.setdefault(tid, []).append({"status": "executing"})
                ws = state.connected.get(tid)
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
                
                run_ret = await loop_inner.run_in_executor(None, lambda: backend_module.backend.run(circuits, shots=1000).result())
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
                state.pending_results[tid] = result

                # Send done to connected websocket if present
                ws = state.connected.get(tid)
                if ws:
                    try:
                        await ws.send_json({"status": "done", "result": result})
                        await ws.close()
                    except Exception as e:
                        logging.info(f"Could not send 'done' to {tid}: {e}")

                # Update leaderboard
                state.leaderboard.append(
                    {
                        "username": t.get("username"),
                        "q1": (backend_module.backend._idx_to_qb[int(t.get("q1"))][2::] if backend_module.backend else int(t.get("q1"))),
                        "q2": (backend_module.backend._idx_to_qb[int(t.get("q2"))][2::] if backend_module.backend else int(t.get("q2"))),
                        "result": result,
                        "image": state.transpiled_images.get(tid),
                    }
                )

                state.leaderboard.sort(key=lambda x: x["result"].get("00", 0 + x["result"].get("11", 0)), reverse=True)

                logging.info([(x["username"], x["result"]) for x in state.leaderboard])

                if len(state.leaderboard) > MAX_LEADERBOARD_SIZE:
                    state.leaderboard.pop()

                state.transpiled_images.pop(tid, None)

            logging.info(f"Finished sub-batched run for {batch_size} circuits")

        # After processing all sub-batches, sleep only the remainder of the interval.
        elapsed = loop.time() - start
        sleep_time = max(0, BATCH_INTERVAL_SECONDS - elapsed)
        if sleep_time:
            await asyncio.sleep(sleep_time)