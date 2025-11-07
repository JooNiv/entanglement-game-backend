import asyncio
import logging
import src.state as state
from src.config import TEST, BATCH_INTERVAL_SECONDS, BATCH_MAX_CIRCUITS, MAX_LEADERBOARD_SIZE
import src.backend as backend_module
from qiskit.result import Result
from qiskit.result.models import ExperimentResult, ExperimentResultData, QobjExperimentHeader


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
                if TEST:
                    await asyncio.sleep(3)
                    
                    fake_res = Result(backend_name='aer_simulator', backend_version='0.13.3', qobj_id='', job_id='f370e58e-6e74-497b-b598-95145037111d', success=True, results=[ExperimentResult(shots=10, success=True, meas_level=2, data=ExperimentResultData(counts={'0x0': 400, '0x1':50, '0x2': 50, '0x3': 400}), header=QobjExperimentHeader(creg_sizes=[['c', 2]], global_phase=0.0, memory_slots=2, n_qubits=54, name='circuit-19120', qreg_sizes=[['control', 1], ['ancilla', 52], ['target', 1]], metadata={}), status="DONE", seed_simulator=2951211792, metadata={'time_taken': 0.022419774, 'num_bind_params': 1, 'parallel_state_update': 8, 'parallel_shots': 1, 'required_memory_mb': 1, 'input_qubit_map': [[1, 1], [0, 0]], 'method': 'density_matrix', 'device': 'CPU', 'num_qubits': 2, 'sample_measure_time': 0.000954547, 'active_input_qubits': [0, 1], 'num_clbits': 2, 'remapped_qubits': False, 'runtime_parameter_bind': False, 'max_memory_mb': 7644, 'noise': 'superop', 'measure_sampling': True, 'batched_shots_optimization': False, 'fusion': {'applied': False, 'max_fused_qubits': 2, 'threshold': 7, 'enabled': True}}, time_taken=0.022419774)], date="2025-10-30T11:26:54.745781", status="COMPLETED", header=None, metadata={'time_taken_parameter_binding': 0.000692017, 'time_taken_execute': 0.224359233, 'omp_enabled': True, 'max_gpu_memory_mb': 0, 'max_memory_mb': 7644, 'parallel_experiments': 1}, time_taken=6.077264070510864)

                    fake_res.results= len(circuits)*[fake_res.results[0]]
                    run_ret = fake_res
                else:
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

                if len(state.leaderboard) > MAX_LEADERBOARD_SIZE:
                    state.leaderboard.pop(0)

                state.transpiled_images.pop(tid, None)

            logging.info(f"Finished sub-batched run for {batch_size} circuits")

        # After processing all sub-batches, sleep only the remainder of the interval.
        elapsed = loop.time() - start
        sleep_time = max(0, BATCH_INTERVAL_SECONDS - elapsed)
        if sleep_time:
            await asyncio.sleep(sleep_time)