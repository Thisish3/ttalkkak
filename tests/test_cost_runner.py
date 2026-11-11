import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from ttalkkak.cost.power import TS_FORMAT, parse_nvsmi_csv
from ttalkkak.cost.runner import guidellm_cmd, parse_energy_run, parse_points, run_cost

FIX = Path(__file__).parent / "fixtures"
BASE = json.loads((FIX / "guidellm/report_mock.json").read_text())
T0 = 1_800_000_000.0


def report(streams, t0=T0, prompt=256, cached=0, output=128):
    r = copy.deepcopy(BASE)
    template = r["benchmarks"][0]
    r["benchmarks"] = []
    for n, s in enumerate(streams):
        b = copy.deepcopy(template)
        b["config"]["strategy"] = {"type_": "concurrent", "streams": s}
        b["start_time"], b["end_time"] = t0 + n * 20, t0 + n * 20 + 10
        m = b["metrics"]
        m["output_tokens_per_second"]["successful"]["mean"] = 100.0 * s
        m["time_to_first_token_ms"]["successful"]["percentiles"]["p99"] = 100.0 * s
        m["inter_token_latency_ms"]["successful"]["percentiles"]["p99"] = 20.0 * s
        m["prompt_token_count"]["successful"]["total_sum"] = prompt * 10
        m["cached_token_count"]["successful"]["total_sum"] = cached * 10
        m["output_token_count"]["successful"]["total_sum"] = output * 10
        r["benchmarks"].append(b)
    return r


def csv(t0, t1, watts=100.0):
    rows = ["timestamp, power.draw [W], clocks.current.sm [MHz], temperature.gpu"]
    t = t0
    while t <= t1:
        ts = datetime.fromtimestamp(t).strftime(TS_FORMAT)[:-3]
        rows.append(f"{ts}, {watts:.2f} W, 1500 MHz, 50")
        t += 0.5
    return "\n".join(rows) + "\n"


def test_parse_points_from_concurrent_report():
    pts = parse_points(report([1, 2, 4]))
    assert [p.streams for p in pts] == [1, 2, 4]
    assert pts[2].ttft_p99_ms == 400 and pts[2].itl_p99_ms == 80
    assert pts[1].output_tok_s == 200


def test_energy_run_sums_benchmark_windows():
    r = report([1, 2])
    samples = parse_nvsmi_csv(csv(T0 - 1, T0 + 40))
    e = parse_energy_run(r, samples)
    assert e.energy_j == pytest.approx(100 * 20, rel=1e-3)
    assert e.input_tokens == 2 * 2560 and e.output_tokens == 2 * 1280


def test_guidellm_cmd_sweeps_streams_and_shared_prefix():
    cmd = guidellm_cmd(
        "http://h:8000/v1", "m", "kind=synthetic_text,x=1", Path("o.json"), [1, 2], 60
    )
    s = " ".join(cmd)
    assert "kind=concurrent" in s and "profile.streams 1,2" in s
    assert "target=http://h:8000," in s and "max_duration,seconds=60" in s


class FakePopen:
    def __init__(self, text):
        self.text, self.terminated = text, False

    def terminate(self):
        self.terminated = True

    def communicate(self, timeout=None):
        return self.text, ""


def test_run_cost_writes_json_and_markdown(tmp_path, monkeypatch):
    run_dir = tmp_path / "r1"
    shutil.copytree(FIX / "runs/vllm-nvidia", run_dir)
    monkeypatch.setattr(
        "ttalkkak.cost.runner.resolve_endpoint",
        lambda d: ("http://h:8000/v1", "m", "EMPTY"),
    )
    reports = {
        "A": report([1, 2, 4], T0, 256, 0, 128),
        "B": report([8], T0 + 100, 2048, 0, 1),
        "C": report([4], T0 + 200, 2048, 1792, 128),
    }

    def fake_run(cmd, **kw):
        out = next(a.split("path=")[1] for a in cmd if a.startswith("kind=json,path="))
        Path(out).write_text(json.dumps(reports[Path(out).stem[-1]]))

    popen = FakePopen(csv(T0 - 1, T0 + 260))
    cfg = tmp_path / "cost.yaml"
    result, created = run_cost(run_dir, cfg, run=fake_run, popen=lambda *a, **k: popen)
    assert created and popen.terminated
    saved = json.loads((run_dir / "cost.json").read_text())
    assert saved["price_krw_per_mtok"]["output"] == pytest.approx(
        result.price_krw_per_mtok.output
    )
    md = (run_dir / "cost.md").read_text()
    assert "원/100만 토큰" in md and "운영점" in md and "한계" in md
    assert (run_dir / "cost" / "power.csv").exists()
