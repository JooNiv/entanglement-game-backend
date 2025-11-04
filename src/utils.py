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

def remove_idle_qwires(circ: QuantumCircuit):
    active_qubits = find_active_qubits(circ)
    qrs = [QuantumRegister(1, i) for i in active_qubits]
    cr = ClassicalRegister(2, "c")
    new_qc = QuantumCircuit(*qrs, cr)
    for inst in circ.data:
        qubits = [active_qubits.index(circ.find_bit(j).index) for j in inst.qubits]
        new_instruction = CircuitInstruction(inst.operation, qubits, inst.clbits)
        new_qc.append(new_instruction)
    return new_qc

def render_circuit_image(circuit: QuantumCircuit) -> str:
    fig = circuit.draw(output="mpl")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")