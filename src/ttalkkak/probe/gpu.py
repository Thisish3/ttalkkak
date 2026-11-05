import subprocess

from ttalkkak.schemas import GpuInfo

QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,compute_cap",
    "--format=csv,noheader,nounits",
]


def parse_nvidia_smi(csv: str) -> GpuInfo:
    name, mem, cc = [p.strip() for p in csv.strip().splitlines()[0].split(",")]
    return GpuInfo(name=name, memory_mib=int(mem), compute_capability=cc)


def detect_gpu(run=subprocess.run) -> GpuInfo | None:
    try:
        out = run(QUERY, capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return parse_nvidia_smi(out.stdout)
