from iqm.qiskit_iqm import IQMFakeAphrodite, IQMProvider
from src.config import QX_TOKEN, DEVICE, TEST
import logging
import os
from typing import Tuple, Optional
from iqm.qiskit_iqm import IQMFakeAphrodite, IQMProvider

logger = logging.getLogger(__name__)

# Module-level state
backend = None  # will hold IQM backend or IQMFakeAphrodite instance

def init_backend(device: Optional[str] = None, qx_token: Optional[str] = None, test: Optional[bool] = None) -> None:
    """
    Initialize the module-level backend according to provided parameters or environment.
    Call this during app startup. This function is idempotent.
    """
    global backend, DEVICE, QX_TOKEN, TEST

    if device is not None:
        DEVICE = device
    if qx_token is not None:
        QX_TOKEN = qx_token
    if test is not None:
        TEST = test

    # Prefer explicit simulator for TEST or when no valid QX token/device provided.
    if TEST or not QX_TOKEN or DEVICE == "simulator":
        backend = IQMFakeAphrodite()
        DEVICE = "simulator"
        logger.info("Using simulator backend")
        return

    # Try to connect to IQMProvider
    try:
        server_url = f"https://qx.vtt.fi/api/devices/{DEVICE}"
        provider = IQMProvider(server_url)
        backend = provider.get_backend()
        logger.info(f"Connected to IQM backend: {DEVICE}")
    except Exception as exc:
        logger.exception("Failed to connect to IQM backend, falling back to simulator")
        backend = IQMFakeAphrodite()
        DEVICE = "simulator"

def get_backend():
    """Return the current backend instance (may be simulator)."""
    if backend is None:
        # Lazy init from env if not initialized explicitly
        init_backend()
    return backend

def validate_qx_token(token: str) -> bool:
    """
    Quick check that the provided token can be used to fetch a test backend.
    Returns True on success, False otherwise. Does not mutate module state.
    """
    prev_token = os.environ.get("IQM_TOKEN")
    if not token:
        return False
    try:
        os.environ["IQM_TOKEN"] = token
        # Try to fetch the demo device as a lightweight validation
        server_url = "https://qx.vtt.fi/api/devices/demo"
        provider = IQMProvider(server_url)
        _ = provider.get_backend()
        return True
    except Exception:
        logger.exception("QX token validation failed")
        os.environ["IQM_TOKEN"] = prev_token or ""
        return False

def set_device(device: str, qx_token: Optional[str] = None) -> dict:
    """
    Switch backend device. Returns a dict describing the result.
    """
    global DEVICE
    global QX_TOKEN
    prev = DEVICE

    logger.info(QX_TOKEN)

    if not QX_TOKEN or QX_TOKEN.strip() == "":
        return {"device": DEVICE, "error": "No QX token set, cannot switch device."}

    if device == prev:
        return {"device": DEVICE}

    # try to switch; on failure revert to previous
    try:
        init_backend(device=device, qx_token=qx_token or QX_TOKEN)
    except Exception as exc:
        logger.exception("Error switching device")
        init_backend(device=prev, qx_token=qx_token or QX_TOKEN)
        return {"device": prev, "error": "Could not connect to device, reverted to previous."}

    return {"device": DEVICE}

# Qubit mapping helpers

class QubitMappingError(ValueError):
    pass

def max_user_qubit() -> int:
    """Return max user-visible qubit index (integer). Raises QubitMappingError if backend can't provide it."""
    b = get_backend()
    try:
        qubits = getattr(b, "architecture").qubits
        # qubit names like "QB1", "QB2" -> take numeric part
        return max(int(name[2:]) for name in qubits)
    except Exception as exc:
        logger.exception("Failed to determine max qubit")
        raise QubitMappingError("Backend does not expose architecture.qubits")

def map_user_qubits(q1: int, q2: int) -> Tuple[int, int]:
    """
    Map user-visible 1-based qubit numbers (q1, q2) to backend internal indices.
    Raises QubitMappingError with readable message on invalid input.
    """
    if q1 <= 0 or q2 <= 0:
        raise QubitMappingError("Qubit indices must be positive integers")

    b = get_backend()
    try:
        max_q = max_user_qubit()
    except QubitMappingError:
        raise

    if q1 > max_q or q2 > max_q:
        raise QubitMappingError(f"Qubit indices must be <= {max_q}")

    try:
        idx1 = b._qb_to_idx[f"QB{q1}"]
    except Exception:
        raise QubitMappingError(f"Qubit {q1} is offline or unavailable")

    try:
        idx2 = b._qb_to_idx[f"QB{q2}"]
    except Exception:
        raise QubitMappingError(f"Qubit {q2} is offline or unavailable")

    return idx1, idx2