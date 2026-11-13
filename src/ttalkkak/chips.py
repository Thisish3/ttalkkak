import re
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from ttalkkak.errors import TtalkkakError

_BUILTIN = ("nvidia-gpu", "amd-gpu")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Detect(_Strict):
    command: list[str]
    parser: str | None = None
    name: str | None = None
    memory_mib: str | None = None


class EngineSpec(_Strict):
    image: str
    api: Literal["openai"]
    port: int = 8000
    args: list[str] = []
    health: str = "/health"


class DockerSpec(_Strict):
    flags: list[str] = []
    devices: list[str] = []


class K8sSpec(_Strict):
    resource: str


class Telemetry(_Strict):
    power_watts: list[str] | None = None
    exporter_image: str | None = None
    metrics: dict[
        Literal["utilization", "power", "memory_used", "temperature"], str
    ] = {}


class ChipConfig(_Strict):
    name: str
    vendor: str
    detect: Detect
    engines: dict[str, EngineSpec]
    docker: DockerSpec
    kubernetes: K8sSpec
    telemetry: Telemetry
    export: dict[str, str] = {}


_LEGACY = {"nvidia": "nvidia-gpu", "amd": "amd-gpu"}
_DEFAULT_CHIP = "nvidia-gpu"


def _korean(err: dict) -> str:
    kind = err["type"]
    if kind == "missing":
        return "필수 칸이 없습니다"
    if kind == "extra_forbidden":
        return "알 수 없는 칸입니다"
    if kind == "literal_error":
        return f"허용되지 않는 값입니다: {err.get('ctx', {}).get('expected', '')}"
    if kind.endswith("_type") or kind.endswith("_parsing"):
        return "형식이 맞지 않습니다"
    return err["msg"]


def load_chip(path: Path) -> ChipConfig:
    label = f"chips/{path.name}"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise TtalkkakError(f"{label}: 파일을 읽을 수 없습니다: {e}", 2) from e
    if not isinstance(raw, dict):
        raise TtalkkakError(f"{label}: 최상위가 칸 목록(매핑)이어야 합니다", 2)
    try:
        chip = ChipConfig.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{label}: {'.'.join(str(p) for p in err['loc'])}: {_korean(err)}"
            for err in e.errors()
        ]
        raise TtalkkakError("\n".join(lines), 2) from e
    if not chip.engines:
        raise TtalkkakError(f"{label}: engines: 엔진이 하나 이상 필요합니다", 2)
    return chip


@cache
def _builtin() -> tuple[ChipConfig, ...]:
    root = files("ttalkkak.data").joinpath("chips")
    return tuple(load_chip(Path(str(root.joinpath(f"{n}.yaml")))) for n in _BUILTIN)


def builtin_chips() -> list[ChipConfig]:
    return [c.model_copy(deep=True) for c in _builtin()]


def chip_by_name(name: str) -> ChipConfig:
    for c in builtin_chips():
        if c.name == name:
            return c
    raise TtalkkakError(f"칩 설정을 찾을 수 없습니다: {name}", 2)


def resolve_chip_name(vendor: str, chip: str | None = None) -> str:
    """Chip name from a chip name or a legacy "nvidia"/"amd" vendor."""
    if chip in (None, _DEFAULT_CHIP) and vendor.lower() in _LEGACY:
        return _LEGACY[vendor.lower()]
    return chip or _LEGACY.get(vendor.lower(), vendor)


_STEP = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]")


def json_path(doc: Any, path: str) -> Any:
    if not path.startswith("$"):
        return None
    rest, cur = path[1:], doc
    while rest:
        m = _STEP.match(rest)
        if not m:
            return None
        key, idx = m.groups()
        try:
            cur = cur[key] if key is not None else cur[int(idx)]
        except (KeyError, IndexError, TypeError):
            return None
        rest = rest[m.end() :]
    return cur
