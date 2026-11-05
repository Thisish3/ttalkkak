import json
import subprocess

from ttalkkak.schemas import GpuInfo

QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,compute_cap",
    "--format=csv,noheader,nounits",
]
AMD_QUERY = ["amd-smi", "static", "--asic", "--vram", "--json"]
_MIB = {"MB": 1, "MiB": 1, "GB": 1024, "GiB": 1024}


def parse_nvidia_smi(csv: str) -> GpuInfo:
    name, mem, cc = [p.strip() for p in csv.strip().splitlines()[0].split(",")]
    return GpuInfo(name=name, memory_mib=int(mem), compute_capability=cc)


def parse_amd_smi(json_text: str) -> GpuInfo:
    first = json.loads(json_text)[0]
    size = first["vram"]["size"]
    return GpuInfo(
        name=first["asic"]["market_name"],
        memory_mib=int(size["value"] * _MIB.get(size.get("unit", "MB"), 1)),
        compute_capability="",
        vendor="amd",
        arch=first["asic"].get("target_graphics_version", ""),
    )


def detect_gpu(run=subprocess.run) -> GpuInfo | None:
    try:
        out = run(QUERY, capture_output=True, text=True, timeout=10, check=True)
        return parse_nvidia_smi(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        out = run(AMD_QUERY, capture_output=True, text=True, timeout=10, check=True)
        return parse_amd_smi(out.stdout)
    except (
        FileNotFoundError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        IndexError,
    ):
        return None
