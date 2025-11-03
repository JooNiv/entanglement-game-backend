import logging

handler = logging.StreamHandler()
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)

root = logging.getLogger()
root.handlers = [handler]
root.setLevel(logging.INFO)
root.propagate = False

for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False

# quiet noisy libraries (Qiskit, matplotlib, etc.)
noisy = (
    "qiskit",
    "qiskit.passmanager",
    "qiskit.transpiler",
    "qiskit.transpiler.passes",
    "qiskit.passmanager.base_tasks",
    "qiskit.transpiler.passes.basis",
    "matplotlib",
)
for name in noisy:
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.setLevel(logging.WARNING)
    lg.propagate = False

logger = logging.getLogger(__name__)