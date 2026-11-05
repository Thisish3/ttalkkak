import json
from pathlib import Path

import httpx
import pytest
from mock_openai import mock_transport

from ttalkkak.pipeline import Deps, run_up
from ttalkkak.schemas import GpuInfo, Launched, ModelInfo, ProbeResult

M = ModelInfo(
    model_id="Qwen/Qwen2.5-1.5B-Instruct",
    family="qwen2",
    num_layers=28,
    num_kv_heads=2,
    head_dim=128,
    dtype="bfloat16",
    quantization=None,
    max_position=32768,
    weight_bytes=3_100_000_000,
)
G = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")
SKILLS = Path(__file__).parent / "fixtures/skills"


def deps(stopped, launch_status="ready", interrupt=False):
    def launch(c, adapter, **kw):
        if interrupt:
            raise KeyboardInterrupt
        return Launched(
            candidate_id=c.id,
            status=launch_status,
            container=f"k{c.id}",
            base_url=f"http://h/{c.id}",
            collected={},
            logs="",
        )

    return Deps(
        probe=lambda m: ProbeResult(model=M, gpu=G),
        check_docker=lambda: None,
        launch=launch,
        stop=lambda cid: stopped.append(cid),
        client=httpx.Client(transport=mock_transport("good")),
        run_accuracy=lambda url, model, out: 0.6,
        run_perf=lambda url, model, out: {
            "output_tok_s": 10.0 if "/c1/" in url else 20.0
        },
    )


def test_up_writes_all_stage_files_and_keeps_chosen(tmp_path):
    stopped = []
    run_dir, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped))
    for f in [
        "probe.json",
        "plan.json",
        "launch.json",
        "verify.json",
        "pick.json",
        "report.md",
        "state.json",
    ]:
        assert (run_dir / f).exists(), f
    assert p.chosen == "c2" and stopped == ["kc1", "kc2", "kc3"]


def test_interrupt_cleans_up_and_saves_state(tmp_path):
    stopped = []
    with pytest.raises(KeyboardInterrupt):
        run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped, interrupt=True))
    state = json.loads(next(tmp_path.glob("*/state.json")).read_text())
    assert state["done"] == ["probe", "plan"]


def test_resume_skips_done_stages(tmp_path):
    stopped = []
    run_dir, _ = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped))
    calls = []
    d = deps([])
    d.probe = lambda m: calls.append(m)
    run_up(M.model_id, ["tooly"], tmp_path, SKILLS, d, resume=run_dir.name)
    assert calls == []


class Gpu:
    """Fake docker that tracks how many candidate servers run at once."""

    def __init__(self):
        self.active, self.peak, self.stopped = set(), 0, []

    def launch(self, c, adapter, **kw):
        cid = f"k{c.id}-{len(self.stopped) + len(self.active)}"
        self.active.add(cid)
        self.peak = max(self.peak, len(self.active))
        return Launched(
            candidate_id=c.id,
            status="ready",
            container=cid,
            base_url=f"http://h/{c.id}",
            collected={},
            logs="",
        )

    def stop(self, cid):
        self.active.discard(cid)
        self.stopped.append(cid)


def gpu_deps(gpu, **kw):
    d = deps([])
    d.launch, d.stop = gpu.launch, gpu.stop
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_candidates_run_one_at_a_time_and_winner_stays_up(tmp_path):
    gpu = Gpu()
    _, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, gpu_deps(gpu))
    assert gpu.peak == 1
    assert p.chosen == "c2" and len(gpu.active) == 1
    assert next(iter(gpu.active)).startswith("kc2") and p.base_url == "http://h/c2"


def test_verify_error_rejects_candidate_and_still_writes_report(tmp_path):
    import subprocess

    def perf(url, model, out):
        if "/c2/" in url:
            raise subprocess.CalledProcessError(2, ["guidellm"])
        return {"output_tok_s": 10.0}

    gpu = Gpu()
    run_dir, p = run_up(
        M.model_id, ["tooly"], tmp_path, SKILLS, gpu_deps(gpu, run_perf=perf)
    )
    assert p.chosen == "c1" and "검증 오류" in p.rejected["c2"]
    assert (run_dir / "report.md").exists() and gpu.peak == 1


def test_baseline_only_candidate_sets_baseline_and_is_never_chosen(tmp_path):
    from ttalkkak.plan.memory import max_context_fit

    heavy = M.model_copy(update={"weight_bytes": 6_270_000_000})
    assert max_context_fit(heavy, G, "auto") < heavy.max_position
    assert max_context_fit(heavy, G, "fp8") > max_context_fit(heavy, G, "auto")
    sk = tmp_path / "skills" / "big"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: big\ndescription: d\n---\nb\n")
    (sk / "ttalkkak.yaml").write_text(
        f"min_context: {max_context_fit(heavy, G, 'auto') + 1}\n"
    )
    seen = []

    def acc(url, model, out):
        seen.append(url)
        return 0.6

    gpu = Gpu()
    _, p = run_up(
        M.model_id,
        ["big"],
        tmp_path / "runs",
        tmp_path / "skills",
        gpu_deps(
            gpu,
            run_accuracy=acc,
            probe=lambda m: ProbeResult(model=heavy, gpu=G),
        ),
    )
    assert any("/c0/" in u for u in seen)
    assert p.chosen == "c2" and gpu.peak == 1


def test_nim_run_writes_report_and_chat_endpoint(tmp_path, monkeypatch):
    from ttalkkak.pipeline import resolve_endpoint

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    run_dir, _ = run_up(
        "nim:meta/llama-3.1-8b-instruct", ["tooly"], tmp_path, SKILLS, deps([])
    )
    assert (run_dir / "report.md").exists()
    url, model, key = resolve_endpoint(run_dir)
    assert url == "https://integrate.api.nvidia.com/v1"
    assert model == "meta/llama-3.1-8b-instruct" and key == "nvapi-test"


def test_local_run_chat_endpoint(tmp_path):
    from ttalkkak.pipeline import resolve_endpoint

    run_dir, _ = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, gpu_deps(Gpu()))
    assert resolve_endpoint(run_dir) == ("http://h/c2/v1", M.model_id, "EMPTY")


def test_resolve_endpoint_missing_run_is_clear_error(tmp_path):
    from ttalkkak.errors import TtalkkakError
    from ttalkkak.pipeline import resolve_endpoint

    with pytest.raises(TtalkkakError, match="run"):
        resolve_endpoint(tmp_path / "nope")
