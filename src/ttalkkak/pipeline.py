import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel

from ttalkkak.engines import ADAPTERS
from ttalkkak.errors import TtalkkakError
from ttalkkak.pick import pick
from ttalkkak.plan.candidates import DEFAULT_GPU_MEMORY_UTILIZATION, make_plan
from ttalkkak.platform import Platform
from ttalkkak.report import render_report
from ttalkkak.schemas import (
    Candidate,
    CandidateVerdict,
    Launched,
    Pick,
    Plan,
    ProbeResult,
    Requirements,
    RunState,
    Verification,
)
from ttalkkak.skills.loader import load_skills
from ttalkkak.skills.requirements import merge_requirements
from ttalkkak.verify.agent import run_agent_checks
from ttalkkak.verify.applied import check_applied

NIM_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class Deps:
    probe: Callable[[str], ProbeResult]
    check_docker: Callable[[], None]
    launch: Callable[..., Launched]
    stop: Callable[[str], None]
    client: httpx.Client
    run_accuracy: Callable[[str, str, Path], float]
    run_perf: Callable[[str, str, Path], dict[str, float]]


class _LaunchList(BaseModel):
    items: list[Launched]


def _save(run_dir: Path, name: str, obj: BaseModel) -> None:
    # atomic, so the UI never reads a half-written file
    tmp = run_dir / f".{name}.tmp"
    tmp.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, run_dir / name)


def _load(run_dir: Path, name: str, cls):
    return cls.model_validate_json((run_dir / name).read_text(encoding="utf-8"))


def run_up(
    model: str,
    skills: list[str],
    runs_root: Path,
    skills_root: Path,
    deps: Deps,
    resume: str | None = None,
    run_id: str | None = None,
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION,
) -> tuple[Path, Pick]:
    run_dir = runs_root / (resume or run_id or time.strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    state = (
        _load(run_dir, "state.json", RunState)
        if resume
        else RunState(run_id=run_dir.name)
    )
    req = merge_requirements(load_skills(skills_root, skills))
    remote = model.startswith("nim:")
    launched: list[Launched] = []
    running: list[str] = []

    def mark(stage: str) -> None:
        if stage not in state.done:
            state.done.append(stage)
        _save(run_dir, "state.json", state)

    def start(c: Candidate) -> Launched:
        item = deps.launch(c, ADAPTERS[c.engine], platform)
        launched.append(item)
        if item.status == "ready" and item.container:
            running.append(item.container)
        return item

    def halt(item: Launched) -> None:
        if item.container in running:
            running.remove(item.container)
            deps.stop(item.container)

    try:
        if not remote and "probe" not in state.done:
            deps.check_docker()
        if "probe" in state.done:
            pr = _load(run_dir, "probe.json", ProbeResult)
        else:
            pr = deps.probe(model)
            _save(run_dir, "probe.json", pr)
            mark("probe")
        if remote:
            return _run_remote(run_dir, model, req, deps)
        if "plan" in state.done:
            plan = _load(run_dir, "plan.json", Plan)
        else:
            plan = make_plan(pr, req, gpu_memory_utilization)
            _save(run_dir, "plan.json", plan)
            mark("plan")
        runnable = [c for c in plan.candidates if not c.blockers]
        platform = Platform.from_gpu(pr.gpu)
        verdicts: list[CandidateVerdict] = []
        errors: dict[str, str] = {}
        baseline = None
        # One candidate on the GPU at a time: launch, verify, stop.
        for c in runnable:
            item = start(c)
            _save(run_dir, "launch.json", _LaunchList(items=launched))
            if item.status != "ready":
                continue
            try:
                url = f"{item.base_url}/v1"
                acc = deps.run_accuracy(url, c.model_id, run_dir / f"acc_{c.id}")
                if c.engine == "vllm" and c.args.get("kv_cache_dtype") == "auto":
                    baseline = acc
                if c.baseline_only:
                    continue
                applied = ADAPTERS[c.engine].read_applied(item.logs, item.collected)
                checked = check_applied(c.engine, c, applied)
                v = CandidateVerdict(
                    candidate_id=c.id,
                    applied=applied,
                    mismatches=checked.get("mismatches", []),
                    applied_check=checked,
                    accuracy=acc,
                )
                v.agent = run_agent_checks(deps.client, url, c.model_id, req)
                v.perf = deps.run_perf(url, c.model_id, run_dir / f"perf_{c.id}.json")
                verdicts.append(v)
            except Exception as err:  # noqa: BLE001
                errors[c.id] = f"검증 오류: {type(err).__name__}: {err}"[:300]
            finally:
                halt(item)
        mark("launch")
        ver = Verification(verdicts=verdicts, baseline_accuracy=baseline)
        _save(run_dir, "verify.json", ver)
        mark("verify")
        urls = {x.candidate_id: x.base_url for x in launched if x.base_url}
        chosen = pick(verdicts, baseline, urls)
        needs_base = {c.id for c in runnable if c.requires_accuracy}
        if baseline is None and chosen.chosen in needs_base:
            rejected = dict(chosen.rejected)
            rejected[chosen.chosen] = "기준 정확도 없음"
            chosen = pick(
                [v for v in verdicts if v.candidate_id not in needs_base], None, urls
            )
            chosen.rejected = rejected | chosen.rejected
        chosen.rejected = errors | chosen.rejected
        if chosen.chosen:
            winner = next(c for c in runnable if c.id == chosen.chosen)
            item = start(winner)
            if item.status == "ready":
                chosen.base_url, chosen.model = item.base_url, winner.model_id
            else:
                chosen.rejected[winner.id] = f"재기동 실패 ({item.failure})"
                chosen.chosen, chosen.base_url = None, None
        _save(run_dir, "launch.json", _LaunchList(items=launched))
        _save(run_dir, "pick.json", chosen)
        mark("pick")
        (run_dir / "report.md").write_text(
            render_report(plan, launched, ver, chosen), encoding="utf-8"
        )
        return run_dir, chosen
    except BaseException:
        for cid in running[:]:
            deps.stop(cid)
        _save(run_dir, "state.json", state)
        raise


def _run_remote(run_dir: Path, model: str, req: Requirements, deps: Deps):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise TtalkkakError("NIM 모델에는 NVIDIA_API_KEY 환경변수가 필요합니다", 2)
    name = model.removeprefix("nim:")
    v = CandidateVerdict(candidate_id="nim")
    v.agent = run_agent_checks(deps.client, NIM_URL, name, req, api_key=key)
    ver = Verification(verdicts=[v])
    _save(run_dir, "verify.json", ver)
    chosen = pick([v], None, {"nim": NIM_URL.removesuffix("/v1")})
    chosen.model = name
    _save(run_dir, "pick.json", chosen)
    plan = Plan(candidates=[], requirements=req)
    (run_dir / "report.md").write_text(
        render_report(plan, [], ver, chosen), encoding="utf-8"
    )
    return run_dir, chosen


def resolve_endpoint(run_dir: Path) -> tuple[str, str, str]:
    """Return (OpenAI base URL, model, API key) of a finished run's endpoint."""
    if not (run_dir / "pick.json").is_file():
        raise TtalkkakError(f"완료된 run이 아닙니다: {run_dir}", 2)
    chosen = _load(run_dir, "pick.json", Pick)
    if not chosen.base_url or not chosen.model:
        raise TtalkkakError("선택된 엔드포인트가 없습니다", 1)
    key = os.environ.get("NVIDIA_API_KEY", "") if chosen.chosen == "nim" else "EMPTY"
    return f"{chosen.base_url}/v1", chosen.model, key
