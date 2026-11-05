from pathlib import Path

import yaml
from pydantic import BaseModel

from ttalkkak.engines import ADAPTERS
from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import Candidate, Pick, Plan, ProbeResult

_DROP_WITH_VALUE = {"--model", "--model-path", "--host", "--port"}
_LAUNCHER = ["python3", "-m", "sglang.launch_server"]


class RunBundle(BaseModel):
    run_id: str
    candidate: Candidate
    vendor: str


def _read(run_dir: Path, name: str, cls):
    path = run_dir / name
    if not path.is_file():
        raise TtalkkakError(f"완료된 run이 아닙니다 ({name} 없음): {run_dir}", 2)
    return cls.model_validate_json(path.read_text(encoding="utf-8"))


def load_run(run_dir: Path) -> RunBundle:
    chosen = _read(run_dir, "pick.json", Pick)
    if not chosen.chosen or chosen.chosen == "nim":
        raise TtalkkakError(f"내보낼 로컬 선택 후보가 없습니다: {run_dir}", 2)
    plan = _read(run_dir, "plan.json", Plan)
    probe = _read(run_dir, "probe.json", ProbeResult)
    cand = next((c for c in plan.candidates if c.id == chosen.chosen), None)
    if cand is None:
        raise TtalkkakError(f"plan.json에 선택 후보 {chosen.chosen}가 없습니다", 2)
    vendor = probe.gpu.vendor if probe.gpu else "nvidia"
    return RunBundle(run_id=run_dir.name, candidate=cand, vendor=vendor)


def engine_args(c: Candidate) -> list[str]:
    """Engine flags from the adapter, without launcher, model, host and port."""
    args = ADAPTERS[c.engine].build_args(c).args
    if args[:3] == _LAUNCHER:
        args = args[3:]
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _DROP_WITH_VALUE:
            skip = True
            continue
        out.append(a)
    return out


def gpu_key(vendor: str) -> str:
    return "amd.com/gpu" if vendor == "amd" else "nvidia.com/gpu"


def dump(docs: list[dict]) -> str:
    return yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True)
