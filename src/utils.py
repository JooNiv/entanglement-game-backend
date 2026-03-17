import io, base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from qiskit import QuantumCircuit
from qiskit.converters import circuit_to_dag
from qiskit.circuit import CircuitInstruction
from qiskit import QuantumRegister, ClassicalRegister

def find_active_qubits(circuit: QuantumCircuit):
    dag = circuit_to_dag(circuit)
    return [circuit.find_bit(qubit).index for qubit in circuit.qubits if qubit not in dag.idle_wires()]

def _count_gates(circuit: QuantumCircuit):
    """Count the number of gates acting on each qubit in a QuantumCircuit.

    Args:
        circuit (QuantumCircuit): The input quantum circuit.

    Returns:
        dict[Qubit, int]: A dictionary mapping each qubit to the number of gates 
        acting on it.
    """
    gate_count = dict.fromkeys(circuit.qubits, 0)
    for instruction in circuit.data:
        for qubit in instruction.qubits:
            gate_count[qubit] += 1

    return gate_count


def remove_idle_qwires(circuit: QuantumCircuit) -> QuantumCircuit:
    """Remove idle wires from a QuantumCircuit.

    Args:
        circuit (QuantumCircuit): The input quantum circuit.

    Returns:
        QuantumCircuit: A new quantum circuit with idle wires removed.
    """
    gate_count = _count_gates(circuit)
    for qubit, count in gate_count.items():
        if count == 0:
            circuit.qubits.remove(qubit)
    
    return circuit

def render_circuit_image(circuit: QuantumCircuit) -> str:
    fig = circuit.draw(output="mpl")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")