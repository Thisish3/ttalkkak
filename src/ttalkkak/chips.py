import os
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
    if kind.endswith(("_type", "_parsing")):
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
    empty = f"{label}: engines: 엔진이 하나 이상 필요합니다"
    try:
        chip = ChipConfig.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{label}: {'.'.join(str(p) for p in err['loc'])}: {_korean(err)}"
            for err in e.errors()
        ]
        if raw.get("engines") == {}:
            lines.append(empty)
        raise TtalkkakError("\n".join(lines), 2) from e
    if not chip.engines:
        raise TtalkkakError(empty, 2)
    return chip


@cache
def _builtin() -> tuple[ChipConfig, ...]:
    root = files("ttalkkak.data").joinpath("chips")
    return tuple(load_chip(Path(str(root.joinpath(f"{n}.yaml")))) for n in _BUILTIN)


def builtin_chips() -> list[ChipConfig]:
    return [c.model_copy(deep=True) for c in _builtin()]


_EXTRA: dict[str, ChipConfig] = {}


def _user_dirs() -> list[Path]:
    dirs = [Path.home() / ".ttalkkak" / "chips"]
    if env := os.environ.get("TTALKKAK_CHIPS_DIR"):
        dirs.append(Path(env))
    return dirs


def available_chips() -> list[ChipConfig]:
    """Built-ins plus user chip files; a user file overrides a built-in by name."""
    chips = {c.name: c for c in builtin_chips()}
    for d in _user_dirs():
        if d.is_dir():
            for f in sorted(d.glob("*.yaml")):
                chip = load_chip(f)
                chips[chip.name] = chip
    chips.update(_EXTRA)
    return list(chips.values())


def chip_by_name(name: str) -> ChipConfig:
    for c in available_chips():
        if c.name == name:
            return c
    raise TtalkkakError(f"칩 설정을 찾을 수 없습니다: {name}", 2)


def chip_from_ref(ref: str) -> ChipConfig:
    """A chip by name, or by path to a yaml file (registered for this process)."""
    if ref.endswith((".yaml", ".yml")) or Path(ref).is_file():
        chip = load_chip(Path(ref))
        _EXTRA[chip.name] = chip
        return chip
    return chip_by_name(ref)


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
