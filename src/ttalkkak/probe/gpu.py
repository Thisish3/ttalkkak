import json
import subprocess

from ttalkkak.chips import ChipConfig, builtin_chips, json_path
from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import GpuInfo

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
        chip="amd-gpu",
        arch=first["asic"].get("target_graphics_version", ""),
    )


_PARSERS = {"nvidia-smi-csv": parse_nvidia_smi, "amd-smi-json": parse_amd_smi}


def _parse(chip: ChipConfig, text: str) -> GpuInfo:
    d = chip.detect
    if d.parser in _PARSERS:
        g = _PARSERS[d.parser](text)
    elif d.name and d.memory_mib:
        doc = json.loads(text)
        name, mem = json_path(doc, d.name), json_path(doc, d.memory_mib)
        if name is None or mem is None:
            raise ValueError("detect paths matched nothing")
        g = GpuInfo(name=str(name), memory_mib=int(mem), compute_capability="")
    else:
        raise ValueError("no detect parser")
    return g.model_copy(update={"chip": chip.name, "vendor": chip.vendor.lower()})


def detect_gpu(
    run=subprocess.run,
    chips: list[ChipConfig] | None = None,
    prefer: str | None = None,
) -> GpuInfo | None:
    chips = builtin_chips() if chips is None else chips
    if prefer is not None:
        chips = [c for c in chips if c.name == prefer]
        if not chips:
            raise TtalkkakError(f"칩 설정을 찾을 수 없습니다: {prefer}", 2)
    for chip in chips:
        try:
            out = run(
                chip.detect.command,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            return _parse(chip, out.stdout)
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ):
            continue
    return None
