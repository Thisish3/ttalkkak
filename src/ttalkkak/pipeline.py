import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.pick import pick
from ttalkkak.plan.candidates import make_plan
from ttalkkak.report import render_report
from ttalkkak.schemas import (
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
from ttalkkak.verify.applied import compare_applied

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
    (run_dir / name).write_text(obj.model_dump_json(indent=2), encoding="utf-8")


def _load(run_dir: Path, name: str, cls):
    return cls.model_validate_json((run_dir / name).read_text(encoding="utf-8"))


def run_up(
    model: str,
    skills: list[str],
    runs_root: Path,
    skills_root: Path,
    deps: Deps,
    resume: str | None = None,
    keep_all: bool = False,
) -> tuple[Path, Pick]:
    run_dir = runs_root / (resume or time.strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    state = (
        _load(run_dir, "state.json", RunState)
        if resume
        else RunState(run_id=run_dir.name)
    )
    req = merge_requirements(load_skills(skills_root, skills))
    remote = model.startswith("nim:")
    launched: list[Launched] = []

    def mark(stage: str) -> None:
        if stage not in state.done:
            state.done.append(stage)
        _save(run_dir, "state.json", state)

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
            plan = make_plan(pr, req)
            _save(run_dir, "plan.json", plan)
            mark("plan")
        runnable = [c for c in plan.candidates if not c.blockers]
        adapter = VllmAdapter()
        for c in runnable:
            launched.append(deps.launch(c, adapter))
        _save(run_dir, "launch.json", _LaunchList(items=launched))
        mark("launch")
        verdicts, baseline = [], None
        for c in runnable:
            item = next(x for x in launched if x.candidate_id == c.id)
            if item.status != "ready":
                continue
            url = f"{item.base_url}/v1"
            applied = adapter.read_applied(item.logs, item.metrics, item.models_json)
            v = CandidateVerdict(
                candidate_id=c.id,
                applied=applied,
                mismatches=compare_applied(c, applied),
            )
            v.agent = run_agent_checks(deps.client, url, c.model_id, req)
            v.accuracy = deps.run_accuracy(url, c.model_id, run_dir / f"acc_{c.id}")
            if c.args.get("kv_cache_dtype") == "auto":
                baseline = v.accuracy
            v.perf = deps.run_perf(url, c.model_id, run_dir / f"perf_{c.id}.json")
            verdicts.append(v)
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
        _save(run_dir, "pick.json", chosen)
        mark("pick")
        (run_dir / "report.md").write_text(
            render_report(plan, launched, ver, chosen), encoding="utf-8"
        )
        for x in launched:
            if (
                x.container
                and x.status == "ready"
                and x.candidate_id != chosen.chosen
                and not keep_all
            ):
                deps.stop(x.container)
        return run_dir, chosen
    except BaseException:
        for x in launched:
            if x.container:
                deps.stop(x.container)
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
    _save(run_dir, "pick.json", chosen)
    return run_dir, chosen
