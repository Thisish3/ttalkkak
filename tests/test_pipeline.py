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
            models_json={},
            logs="",
            metrics="",
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
    assert p.chosen == "c2" and stopped == ["kc1"]


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
