import re
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


def load_chip(path: Path) -> ChipConfig:
    label = f"chips/{path.name}"
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise TtalkkakError(f"{label}: 파일을 읽을 수 없습니다: {e}", 2) from e
    if not isinstance(raw, dict):
        raise TtalkkakError(f"{label}: 최상위가 칸 목록(매핑)이어야 합니다", 2)
    try:
        chip = ChipConfig.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise TtalkkakError(f"{label}: {loc}: {first['msg']}", 2) from e
    if not chip.engines:
        raise TtalkkakError(f"{label}: engines: 엔진이 하나 이상 필요합니다", 2)
    return chip


def builtin_chips() -> list[ChipConfig]:
    root = files("ttalkkak.data").joinpath("chips")
    return [load_chip(Path(str(root.joinpath(f"{n}.yaml")))) for n in _BUILTIN]


def chip_by_name(name: str) -> ChipConfig:
    for c in builtin_chips():
        if c.name == name:
            return c
    raise TtalkkakError(f"칩 설정을 찾을 수 없습니다: {name}", 2)


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
