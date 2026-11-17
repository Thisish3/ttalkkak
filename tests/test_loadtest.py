import copy
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttalkkak.capacity import LOADTEST_TENANT
from ttalkkak.cost.runner import (
    goodput_cmd,
    parse_confirm,
    parse_goodput,
    poisson_cmd,
    throughput_knee,
)
from ttalkkak.errors import TtalkkakError
from ttalkkak.live.loadtest import LoadTestRunner, check_max_users
from ttalkkak.tenants.store import TenantStore

FIX = Path(__file__).parent / "fixtures/guidellm"
# Real guidellm 0.8.1 runs against `guidellm mock-server` with the shipped
# settings (30 s probes incl. 5 s warmup, 60 s poisson check), trimmed to the
# keys the parsers read. See task-G-report.md for the commands.
CONVERGED = json.loads((FIX / "goodput_search.json").read_text())
CONFIRM = json.loads((FIX / "poisson_check.json").read_text())


def out_path(cmd):
    return Path(
        next(a.split("path=")[1] for a in cmd if a.startswith("kind=json,path="))
    )


def profile(cmd):
    return cmd[cmd.index("--profile") + 1]


class Kubectl:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, args, input=None):
        self.calls.append((args, input))
        return SimpleNamespace(returncode=self.rc, stdout="", stderr="")


@pytest.fixture
def store(tmp_path):
    return TenantStore(tmp_path / "t.db")


def runner(tmp_path, store, run, kubectl=None):
    return LoadTestRunner(
        tmp_path, store, "http://gw:8080/v1", "m", run=run,
        now=lambda: datetime(2026, 11, 14, 9, 0, tzinfo=UTC),
        kubectl=kubectl, sync=True,
    )  # fmt: skip


def fake_guidellm(search=CONVERGED, checks=(0.99,), calls=None, lines=None):
    """Search returns `search`; the n-th poisson check gets attainment checks[n]."""
    seen = []

    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if profile(cmd).startswith("kind=goodput"):
            for line in lines or []:
                kw["on_line"](line)
            report = search
        else:
            report = copy.deepcopy(CONFIRM)
            m = report["benchmarks"][0]["metrics"]
            m["slo_attainment"] = checks[min(len(seen), len(checks) - 1)]
            seen.append(cmd)
        out_path(cmd).write_text(json.dumps(report))

    return run


good_run = fake_guidellm()


def candidates(report=CONVERGED):
    probes, _ = parse_goodput(report)
    ok = [p for p in probes if p.passed and p.resolved and not p.aborted]
    return sorted([p for p in ok if p.error_rate == 0], key=lambda p: -p.users)


def check_rate(cmd):
    return float(profile(cmd).split("rate=")[1].split(",")[0])


def applied_capacity(k):
    ((args, manifest),) = [c for c in k.calls if c[0][0] == "apply"]
    assert args == ["apply", "-f", "-"]
    cm = json.loads(manifest)
    assert cm["metadata"]["name"] == "ttalkkak-capacity"
    return json.loads(cm["data"]["capacity.json"])


class SeededKubectl(Kubectl):
    def __init__(self, existing):
        super().__init__()
        self.existing = existing

    def __call__(self, args, input=None):
        r = super().__call__(args, input)
        if args[0] == "get":
            cm = {"data": {"capacity.json": json.dumps(self.existing)}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(cm), stderr="")
        return r


def old_record(model="m", confirmed=True):
    return {
        "loadtest_id": "old", "created_at": "2026-11-01T00:00:00Z", "replicas": 1,
        "model": model, "ttft_target_ms": 1000, "max_tokens_per_min": 10.0,
        "method": "goodput", "confirmed": confirmed,
    }  # fmt: skip


def test_check_max_users():
    for n in (1, 20, 100):
        check_max_users(n)


@pytest.mark.parametrize("n", [0, -1, 101, True])
def test_check_max_users_out_of_range(n):
    with pytest.raises(ValueError):
        check_max_users(n)


def test_runner_searches_confirms_and_writes_capacity(tmp_path, store):
    k, calls = Kubectl(), []
    r = runner(tmp_path, store, fake_guidellm(calls=calls), k)
    tid = r.start(64, replicas=2, autoscale_on=False)
    rec = json.loads((tmp_path / "runs/loadtests" / f"{tid}.json").read_text())
    probes, search = parse_goodput(CONVERGED)
    best = candidates()[0]
    assert rec["method"] == "goodput" and rec["error"] is None
    assert rec["phase"] == "done"
    assert [s["users"] for s in rec["stages"]] == [p.users for p in probes]
    assert rec["stop_reason"] == search["stop_reason"]
    assert rec["capacity_streams"] == best.users
    assert rec["target_attainment"] == 0.95 and rec["ttft_target_ms"] == 1000
    (check,) = rec["confirms"]
    assert check["held"] is True and check["users"] == best.users
    # capacity is what the open-loop run sustained, not the closed-loop probe
    assert rec["capacity_tpm"] == pytest.approx(check["tokens_per_min"])
    search_cmd, confirm_cmd = calls
    assert "max_streams=64" in profile(search_cmd)
    assert check_rate(confirm_cmd) == pytest.approx(best.request_rate * 0.75, rel=1e-3)
    cap = applied_capacity(k)["2"]
    assert cap["loadtest_id"] == tid and cap["replicas"] == 2
    assert cap["method"] == "goodput" and cap["capacity_streams"] == best.users
    assert cap["confirmed"] is True
    assert cap["confirm_rate_rps"] == pytest.approx(check_rate(confirm_cmd))
    assert cap["max_tokens_per_min"] == pytest.approx(check["tokens_per_min"])
    assert r.list()[0]["id"] == tid


def test_check_rate_margin_exceeds_search_tolerance():
    from ttalkkak.live import loadtest

    assert 1 - loadtest.CONFIRM_FACTOR > loadtest.SEARCH_TOLERANCE


def test_open_loop_miss_steps_down_to_the_next_passing_level(tmp_path, store):
    k, calls = Kubectl(), []
    r = runner(tmp_path, store, fake_guidellm(checks=(0.5, 0.99), calls=calls), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    rec = r.status(tid)
    first, second = candidates()[:2]
    assert [c["users"] for c in rec["confirms"]] == [first.users, second.users]
    assert [c["held"] for c in rec["confirms"]] == [False, True]
    assert check_rate(calls[2]) == pytest.approx(second.request_rate * 0.75, rel=1e-3)
    assert rec["capacity_streams"] == second.users
    cap = applied_capacity(k)["1"]
    assert cap["confirmed"] is True and cap["capacity_streams"] == second.users


def test_step_down_is_bounded_and_ends_unconfirmed(tmp_path, store):
    from ttalkkak.live.loadtest import MAX_CONFIRMS

    k, calls = Kubectl(), []
    r = runner(tmp_path, store, fake_guidellm(checks=(0.5,), calls=calls), k)
    rec = r.status(r.start(64, replicas=1, autoscale_on=False))
    n = min(MAX_CONFIRMS, len(candidates()))
    assert len(calls) == 1 + n and len(rec["confirms"]) == n
    assert rec["capacity_streams"] == candidates()[0].users
    assert applied_capacity(k)["1"]["confirmed"] is False


def test_unconfirmed_result_does_not_replace_a_confirmed_record(tmp_path, store):
    k = SeededKubectl({"1": old_record()})
    r = runner(tmp_path, store, fake_guidellm(checks=(0.5,)), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    cap = applied_capacity(k)["1"]
    assert cap["loadtest_id"] == "old" and cap["confirmed"] is True
    assert cap["unconfirmed_attempt"]["loadtest_id"] == tid


def unmet():
    search = copy.deepcopy(CONVERGED)
    c = search["conclusions"][0]
    c["stop_reason"] = "objectives_unmet_at_minimum"
    for p in c["probes"]:
        p["passed"] = False
    return search


def test_unmet_search_writes_a_null_record_for_the_same_model(tmp_path, store):
    k, calls = SeededKubectl({"1": old_record()}), []
    r = runner(tmp_path, store, fake_guidellm(unmet(), calls=calls), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    rec = r.status(tid)
    assert rec["capacity_tpm"] is None and rec["confirms"] == [] and len(calls) == 1
    assert "1명에서도" in rec["no_capacity_reason"]
    cap = applied_capacity(k)["1"]
    assert cap["loadtest_id"] == tid and cap["max_tokens_per_min"] is None
    assert cap["stop_reason"] == "objectives_unmet_at_minimum"


def test_unmet_search_keeps_a_confirmed_record_of_another_model(tmp_path, store):
    k = SeededKubectl({"1": old_record(model="other")})
    runner(tmp_path, store, fake_guidellm(unmet()), k).start(64, 1, False)
    assert applied_capacity(k)["1"]["loadtest_id"] == "old"


def test_indeterminate_search_writes_a_null_record_and_skips_confirm(tmp_path, store):
    search = copy.deepcopy(CONVERGED)
    search["conclusions"][0]["stop_reason"] = "indeterminate_at_minimum"
    k, calls = Kubectl(), []
    r = runner(tmp_path, store, fake_guidellm(search, calls=calls), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    rec = r.status(tid)
    assert rec["capacity_tpm"] is None and rec["confirms"] == []
    assert "판정할 수 없습니다" in rec["no_capacity_reason"]
    assert rec["error"] is None and len(calls) == 1
    assert applied_capacity(k)["1"]["max_tokens_per_min"] is None


def test_confirm_phase_guidellm_failure_is_recorded(tmp_path, store):
    inner = fake_guidellm()

    def run(cmd, **kw):
        if profile(cmd).startswith("kind=poisson"):
            raise subprocess.CalledProcessError(2, cmd)
        inner(cmd, **kw)

    k = Kubectl()
    r = runner(tmp_path, store, run, k)
    rec = r.status(r.start(64, replicas=1, autoscale_on=False))
    assert rec["phase"] == "confirm" and "종료 코드 2" in rec["error"]
    assert rec["running"] is False
    assert not [c for c in k.calls if c[0][0] == "apply"]


def test_progress_phase_is_saved_before_the_confirm_run(tmp_path, store):
    seen = []
    inner = fake_guidellm()

    def run(cmd, **kw):
        if profile(cmd).startswith("kind=poisson"):
            seen.append(r.status(r._active))
        inner(cmd, **kw)

    r = runner(tmp_path, store, run, Kubectl())
    r.start(64, replicas=1, autoscale_on=True)
    (mid,) = seen
    assert mid["phase"] == "confirm" and mid["stages"]


def test_search_progress_follows_guidellms_strategy_lines(tmp_path, store):
    seen = []
    lines = [
        "26-11-17|INFO | x - Starting benchmark for strategy: concurrent@1\n",
        "noise\n",
        "26-11-17|INFO | x - Starting benchmark for strategy: concurrent@2\n",
    ]
    inner = fake_guidellm(lines=lines)

    def run(cmd, **kw):
        def on_line(line):
            kw["on_line"](line)
            seen.append(r.status(r._active).get("probe_now"))

        inner(cmd, **{**kw, "on_line": on_line})

    r = runner(tmp_path, store, run, Kubectl())
    r.start(64, replicas=1, autoscale_on=True)
    from ttalkkak.live.loadtest import MAX_PROBES

    assert seen[0] == {"users": 1, "index": 1, "max": MAX_PROBES}
    assert seen[2] == {"users": 2, "index": 2, "max": MAX_PROBES}


def test_knee_exception_is_logged_and_knee_is_null(tmp_path, store, monkeypatch):
    from ttalkkak.live import loadtest

    def boom(probes):
        raise RuntimeError("knee broke")

    monkeypatch.setattr(loadtest, "throughput_knee", boom)
    r = runner(tmp_path, store, good_run, Kubectl())
    rec = r.status(r.start(64, replicas=1, autoscale_on=True))
    assert rec["knee"] is None and rec["error"] is None and rec["confirms"]


def test_autoscale_on_run_does_not_write_capacity(tmp_path, store):
    k = Kubectl()
    runner(tmp_path, store, good_run, k).start(5, replicas=1, autoscale_on=True)
    assert k.calls == []


def test_stream_run_passes_lines_and_raises_like_subprocess_run():
    import sys

    from ttalkkak.live.loadtest import stream_run

    got = []
    py = [sys.executable, "-c"]
    stream_run(
        py + ["print('a'); print('b')"], check=True, timeout=30, on_line=got.append
    )
    assert [x.strip() for x in got] == ["a", "b"]
    with pytest.raises(subprocess.CalledProcessError):
        stream_run(py + ["raise SystemExit(3)"], check=True, timeout=30)
    with pytest.raises(subprocess.TimeoutExpired):
        stream_run(py + ["import time; time.sleep(5)"], check=True, timeout=0.5)


def test_runner_conflict_409(tmp_path, store):
    r = runner(tmp_path, store, good_run, Kubectl())
    r._active = "other"
    with pytest.raises(TtalkkakError) as err:
        r.start(5, 1, False)
    assert err.value.exit_code == 409


def test_runner_records_error_and_unlocks(tmp_path, store):
    def bad(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    r = runner(tmp_path, store, bad, Kubectl())
    tid = r.start(5, 1, False)
    assert "guidellm" in r.status(tid)["error"]
    assert r.status(tid)["running"] is False
    r.run = good_run
    r.start(5, 1, True)


def test_loadtest_key_revoked_after_run(tmp_path, store):
    seen = []

    def run(cmd, **kw):
        seen.append([k.tenant_id for k in store.active_keys()])
        good_run(cmd, **kw)

    runner(tmp_path, store, run, Kubectl()).start(5, 1, True)
    assert seen and all(s == [LOADTEST_TENANT] for s in seen)
    assert store.active_keys() == []


def test_loadtest_key_revoked_after_failure(tmp_path, store):
    def bad(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    runner(tmp_path, store, bad, Kubectl()).start(5, 1, True)
    assert store.active_keys() == []


def test_list_survives_stray_stage_files_and_bad_records(tmp_path, store):
    r = runner(tmp_path, store, good_run, Kubectl())
    tid = r.start(5, 1, True)
    d = tmp_path / "runs/loadtests"
    (d / "a-1.guidellm.json").write_text("{}")
    (d / "broken.json").write_text("not json")
    (d / "20260101-000000.json").write_text("{}")
    assert [x["id"] for x in r.list()] == [tid]


def test_stage_outputs_are_removed_after_failed_stage(tmp_path, store):
    def bad(cmd, **kw):
        out_path(cmd).write_text("{}")
        raise subprocess.CalledProcessError(1, cmd)

    r = runner(tmp_path, store, bad, Kubectl())
    r.start(5, 1, True)
    assert not list((tmp_path / "runs/loadtests").rglob("*.guidellm.json"))
    assert len(r.list()) == 1


def test_status_rejects_bad_id(tmp_path, store):
    r = runner(tmp_path, store, good_run, Kubectl())
    with pytest.raises(TtalkkakError) as err:
        r.status("../x")
    assert err.value.exit_code == 404


def test_default_clock_is_timezone_aware(tmp_path, store):
    r = LoadTestRunner(tmp_path, store, "http://gw", "m", run=good_run, sync=True)
    tid = r.start(1, 1, True)
    assert datetime.fromisoformat(r.status(tid)["created_at"]).tzinfo is not None


def test_runner_merge_keeps_other_counts(tmp_path, store):
    old = json.loads(
        '{"loadtest_id":"old","created_at":"2026-11-01T00:00:00Z","replicas":1,'
        '"model":"m","ttft_target_ms":500,"max_tokens_per_min":10.0}'
    )

    class K(Kubectl):
        def __call__(self, args, input=None):
            r = super().__call__(args, input)
            if args[0] == "get":
                cm = {"data": {"capacity.json": json.dumps({"1": old})}}
                return SimpleNamespace(returncode=0, stdout=json.dumps(cm), stderr="")
            return r

    k = K()
    tid = runner(tmp_path, store, good_run, k).start(64, replicas=2, autoscale_on=False)
    (_, manifest) = next(c for c in k.calls if c[0][0] == "apply")
    cap = json.loads(json.loads(manifest)["data"]["capacity.json"])
    assert cap["1"]["loadtest_id"] == "old" and cap["2"]["loadtest_id"] == tid


def test_runner_corrupt_capacity_message_names_namespace(tmp_path, store):
    class K(Kubectl):
        def __call__(self, args, input=None):
            super().__call__(args, input)
            if args[0] == "get":
                cm = {"data": {"capacity.json": "{bad"}}
                return SimpleNamespace(returncode=0, stdout=json.dumps(cm), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    r = runner(tmp_path, store, good_run, K())
    tid = r.start(10, replicas=2, autoscale_on=False)
    assert (
        "kubectl delete configmap ttalkkak-capacity -n ttalkkak"
        in r.status(tid)["error"]
    )


def test_runner_read_error_does_not_overwrite_capacity(tmp_path, store):
    class K(Kubectl):
        def __call__(self, args, input=None):
            super().__call__(args, input)
            if args[0] == "get":
                return SimpleNamespace(returncode=1, stdout="", stderr="Forbidden")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    k = K()
    r = runner(tmp_path, store, good_run, k)
    tid = r.start(10, replicas=2, autoscale_on=False)
    assert not [c for c in k.calls if c[0][0] == "apply"]
    assert "저장하지 않았습니다" in r.status(tid)["error"]


def flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def test_goodput_cmd_uses_guidellms_search_with_slo_and_time_probes(tmp_path):
    cmd = goodput_cmd(
        "http://gw:8080/v1", "m", "kind=synthetic_text", tmp_path / "o.json",
        ttft_ms=1000, itl_ms=None, attainment=0.95, max_streams=20,
        probe_seconds=30, warmup_seconds=5, tolerance=0.2, max_probes=10,
        api_key="tk_x",
    )  # fmt: skip
    assert cmd[:2] == ["guidellm", "run"]
    assert flag(cmd, "--backend") == [
        "kind=openai_http,target=http://gw:8080,model=m,api_key=tk_x"
    ]
    assert flag(cmd, "--profile") == [
        (
            "kind=goodput,target_attainment=0.95,initial_streams=1,max_streams=20,"
            "max_probes=10,tolerance=0.2,warmup=5"
        )
    ]
    assert json.loads(flag(cmd, "--metrics")[0]) == {
        "kind": "generative", "slo": {"ttft_ms": 1000}
    }  # fmt: skip
    assert flag(cmd, "--constraint") == ["kind=max_duration,seconds=30"]
    assert "--override" not in cmd and not any("min_requests" in a for a in cmd)
    assert flag(cmd, "--output") == [f"kind=json,path={tmp_path / 'o.json'}"]


def test_goodput_cmd_adds_itl_objective_as_tpot():
    cmd = goodput_cmd(
        "http://gw", "m", "d", Path("o.json"), ttft_ms=800, itl_ms=50,
        attainment=0.9, max_streams=5, probe_seconds=30, warmup_seconds=5,
        tolerance=0.2, max_probes=10,
    )  # fmt: skip
    slo = json.loads(flag(cmd, "--metrics")[0])["slo"]
    assert slo == {"ttft_ms": 800, "tpot_ms": 50}
    assert not any("api_key" in a for a in cmd)


def test_poisson_cmd_runs_one_open_loop_rate_with_warmup_and_the_same_slo():
    cmd = poisson_cmd(
        "http://gw/v1", "m", "d", Path("o.json"), rate=2.5, ttft_ms=1000,
        itl_ms=None, seconds=60, warmup_seconds=5, api_key="k",
    )  # fmt: skip
    assert flag(cmd, "--profile") == ["kind=poisson,rate=2.5,warmup=5"]
    assert flag(cmd, "--constraint") == ["kind=max_duration,seconds=60"]
    assert json.loads(flag(cmd, "--metrics")[0])["slo"] == {"ttft_ms": 1000}


def test_parse_goodput_reads_real_search_conclusion_and_probe_metrics():
    probes, search = parse_goodput(CONVERGED)
    c = CONVERGED["conclusions"][0]
    assert search == {k: c[k] for k in search}
    assert set(search) == {
        "stop_reason",
        "best_passing_streams",
        "lowest_failing_streams",
    }
    assert [p.users for p in probes] == [p["streams"] for p in c["probes"]]
    for p, raw, b in zip(probes, c["probes"], CONVERGED["benchmarks"]):
        assert (p.passed, p.resolved, p.aborted) == (
            raw["passed"], raw["resolved"], raw["aborted"]
        )  # fmt: skip
        assert p.attainment == raw["attainment"]
        assert p.attainment_lower - 1e-9 <= p.attainment <= p.attainment_upper + 1e-9
        ok = b["metrics"]["request_totals"]["successful"]
        assert p.duration_s == b["duration"] >= 20
        assert p.request_rate == pytest.approx(ok / b["duration"])
        assert p.tokens_per_min > 0 and p.output_tok_s > 0


def test_parse_goodput_reads_aborted_probes():
    r = copy.deepcopy(CONVERGED)
    r["conclusions"][0]["probes"][-1]["aborted"] = True
    probes, _ = parse_goodput(r)
    assert probes[-1].aborted and not any(p.aborted for p in probes[:-1])


def test_parse_goodput_without_conclusion_is_a_korean_error():
    with pytest.raises(TtalkkakError, match="탐색 결론"):
        parse_goodput({**CONVERGED, "conclusions": []})


def test_parse_goodput_rejects_probe_benchmark_mismatch():
    r = copy.deepcopy(CONVERGED)
    r["benchmarks"] = r["benchmarks"][:-1]
    with pytest.raises(TtalkkakError, match="맞지 않습니다"):
        parse_goodput(r)


def test_parse_confirm_reads_a_real_open_loop_run():
    b = CONFIRM["benchmarks"][0]
    m = b["metrics"]
    c = parse_confirm(CONFIRM, 0.95)
    assert c["rate_rps"] == b["config"]["strategy"]["rate"]
    assert c["attainment"] == m["slo_attainment"]
    assert c["attainment_lower"] <= c["attainment"] <= c["attainment_upper"]
    assert c["determined_requests"] == m["slo_determined_requests"]
    assert c["held"] is (m["slo_attainment"] >= 0.95 and c["error_rate"] == 0)
    assert c["achieved_rps"] == pytest.approx(
        m["request_totals"]["successful"] / b["duration"]
    )
    assert c["tokens_per_min"] > 0


def test_parse_confirm_holds_only_without_errors():
    r = copy.deepcopy(CONFIRM)
    r["benchmarks"][0]["metrics"]["slo_attainment"] = 0.99
    assert parse_confirm(r, 0.95)["held"] is True
    r["benchmarks"][0]["metrics"]["request_totals"]["errored"] = 3
    assert parse_confirm(r, 0.95)["held"] is False


def test_knee_on_real_probes_reports_points_and_confidence():
    probes, _ = parse_goodput(CONVERGED)
    k = throughput_knee(probes)
    assert k["points"] == len({p.users for p in probes})
    assert k["status"] in {"ok", "no_knee"} and k["low_confidence"] is False


def test_knee_is_low_confidence_on_short_probe_windows():
    probes, _ = parse_goodput(CONVERGED)
    short = [p.model_copy(update={"duration_s": 1.4}) for p in probes]
    assert throughput_knee(short)["low_confidence"] is True


def test_knee_found_on_a_saturating_curve_reuses_probe_throughput():
    probes, _ = parse_goodput(CONVERGED)
    curve = {1: 100, 2: 200, 4: 400, 8: 800, 16: 1000, 32: 1010, 64: 1015}
    shaped = [
        probes[0].model_copy(update={"users": u, "output_tok_s": tok})
        for u, tok in curve.items()
    ]
    k = throughput_knee(shaped)
    assert k["status"] == "ok" and 4 <= k["streams"] <= 16
    assert k["output_tok_s"] in curve.values()


def test_knee_needs_five_levels():
    probes, _ = parse_goodput(CONVERGED)
    k = throughput_knee(probes[:4])
    assert k["status"] == "no_knee" and k["points"] == 4 and "5개" in k["reason"]


def test_fake_console_seeds_goodput_runs_and_record(tmp_path, store):
    import importlib.util

    from ttalkkak.capacity import parse_capacity

    path = Path(__file__).parent.parent / "scripts/fake_console.py"
    spec = importlib.util.spec_from_file_location("fake_console", path)
    fc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fc)
    now = datetime(2026, 11, 17, tzinfo=UTC)
    fc.seed_loadtests(tmp_path, now)
    runs = LoadTestRunner(tmp_path, store, "http://gw", "m").list()
    assert {r["stop_reason"] for r in runs} >= {"converged", "indeterminate_at_minimum"}
    assert any(
        r["confirms"] and not any(c["held"] for c in r["confirms"]) for r in runs
    )
    assert all(r["method"] == "goodput" and r["stages"] for r in runs)
    for state, confirmed in (("unconfirmed", False), (True, True), ("attempt", True)):
        rec = parse_capacity(fc.capacity_json(state, now))[2]
        assert rec.method == "goodput" and rec.confirmed is confirmed
    assert parse_capacity(fc.capacity_json("attempt", now))[2].unconfirmed_attempt


# ---- Task G fix round 2 ----


def test_stream_run_kills_the_process_when_the_line_callback_raises():
    import sys
    import time

    from ttalkkak.live.loadtest import stream_run

    code = "import sys, time; print('x', flush=True); time.sleep(60)"

    def boom(line):
        raise ValueError("callback broke")

    t0 = time.monotonic()
    with pytest.raises(ValueError, match="callback broke"):
        stream_run([sys.executable, "-c", code], check=True, timeout=120, on_line=boom)
    assert time.monotonic() - t0 < 15


def test_probe_seconds_is_chosen_per_test_and_recorded(tmp_path, store):
    calls = []
    r = runner(tmp_path, store, fake_guidellm(calls=calls), Kubectl())
    rec = r.status(r.start(64, replicas=1, autoscale_on=True, probe_seconds=45))
    assert "kind=max_duration,seconds=45" in calls[0]
    assert rec["probe_seconds"] == 45
    rec = r.status(r.start(64, replicas=1, autoscale_on=True))
    from ttalkkak.live.loadtest import PROBE_SECONDS

    assert rec["probe_seconds"] == PROBE_SECONDS == 30


@pytest.mark.parametrize("bad", [5, 301, True, 30.5])
def test_probe_seconds_out_of_range(bad):
    from ttalkkak.live.loadtest import check_probe_seconds

    with pytest.raises(ValueError):
        check_probe_seconds(bad)
