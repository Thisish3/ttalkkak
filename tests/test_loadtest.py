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
CONVERGED = json.loads((FIX / "goodput_converged.json").read_text())
CONFIRM = json.loads((FIX / "poisson_confirm.json").read_text())


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


def fake_guidellm(search=CONVERGED, confirm_attainment=0.99, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if profile(cmd).startswith("kind=goodput"):
            report = search
        else:
            report = copy.deepcopy(CONFIRM)
            report["benchmarks"][0]["metrics"]["slo_attainment"] = confirm_attainment
        out_path(cmd).write_text(json.dumps(report))

    return run


good_run = fake_guidellm()


def applied_capacity(k):
    ((args, manifest),) = [c for c in k.calls if c[0][0] == "apply"]
    assert args == ["apply", "-f", "-"]
    cm = json.loads(manifest)
    assert cm["metadata"]["name"] == "ttalkkak-capacity"
    return json.loads(cm["data"]["capacity.json"])


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
    assert rec["method"] == "goodput" and rec["error"] is None
    assert rec["phase"] == "done"
    assert [s["users"] for s in rec["stages"]] == [
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        48,
        40,
        36,
        38,
    ]
    assert rec["stop_reason"] == "converged" and rec["capacity_streams"] == 36
    p36 = rec["stages"][9]
    assert rec["capacity_tpm"] == pytest.approx(p36["tokens_per_min"])
    assert rec["target_attainment"] == 0.95 and rec["ttft_target_ms"] == 1000
    assert rec["confirm"]["held"] is True
    assert rec["knee"]["status"] == "no_knee"
    search_cmd, confirm_cmd = calls
    assert "max_streams=64" in profile(search_cmd)
    rate = float(profile(confirm_cmd).split("rate=")[1])
    assert rate == pytest.approx(p36["request_rate"] * 0.9, rel=1e-3)
    cap = applied_capacity(k)["2"]
    assert cap["loadtest_id"] == tid and cap["replicas"] == 2
    assert cap["method"] == "goodput" and cap["capacity_streams"] == 36
    assert cap["confirmed"] is True and cap["confirm_rate_rps"] == pytest.approx(rate)
    assert cap["stop_reason"] == "converged" and cap["target_attainment"] == 0.95
    assert cap["max_tokens_per_min"] == pytest.approx(p36["tokens_per_min"])
    assert r.list()[0]["id"] == tid


def test_open_loop_miss_marks_the_record_unconfirmed(tmp_path, store):
    k = Kubectl()
    r = runner(tmp_path, store, fake_guidellm(confirm_attainment=0.7531), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    rec = r.status(tid)
    assert rec["confirm"]["held"] is False and rec["capacity_tpm"] is not None
    assert applied_capacity(k)["1"]["confirmed"] is False


def test_indeterminate_search_writes_no_capacity_and_skips_confirm(tmp_path, store):
    search = copy.deepcopy(CONVERGED)
    search["conclusions"][0]["stop_reason"] = "indeterminate_at_minimum"
    k, calls = Kubectl(), []
    r = runner(tmp_path, store, fake_guidellm(search, calls=calls), k)
    tid = r.start(64, replicas=1, autoscale_on=False)
    rec = r.status(tid)
    assert rec["capacity_tpm"] is None and rec["confirm"] is None
    assert "판정할 수 없습니다" in rec["no_capacity_reason"]
    assert rec["error"] is None and len(calls) == 1
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
    assert mid["phase"] == "confirm" and len(mid["stages"]) == 11


def test_autoscale_on_run_does_not_write_capacity(tmp_path, store):
    k = Kubectl()
    runner(tmp_path, store, good_run, k).start(5, replicas=1, autoscale_on=True)
    assert k.calls == []


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


def test_goodput_cmd_uses_guidellms_search_with_slo_and_probe_limits(tmp_path):
    cmd = goodput_cmd(
        "http://gw:8080/v1", "m", "kind=synthetic_text", tmp_path / "o.json",
        ttft_ms=1000, itl_ms=None, attainment=0.95, max_streams=20,
        probe_seconds=60, probe_requests=150, max_probes=12, api_key="tk_x",
    )  # fmt: skip
    assert cmd[:2] == ["guidellm", "run"]
    assert flag(cmd, "--backend") == [
        "kind=openai_http,target=http://gw:8080,model=m,api_key=tk_x"
    ]
    assert flag(cmd, "--profile") == [
        (
            "kind=goodput,target_attainment=0.95,initial_streams=1,max_streams=20,"
            "max_probes=12"
        )
    ]
    assert json.loads(flag(cmd, "--metrics")[0]) == {
        "kind": "generative", "slo": {"ttft_ms": 1000}
    }  # fmt: skip
    assert flag(cmd, "--constraint") == [
        "kind=max_duration,seconds=60",
        "kind=min_requests,count=150",
    ]
    assert "--override" not in cmd
    assert flag(cmd, "--output") == [f"kind=json,path={tmp_path / 'o.json'}"]


def test_goodput_cmd_adds_itl_objective_as_tpot():
    cmd = goodput_cmd(
        "http://gw", "m", "d", Path("o.json"), ttft_ms=800, itl_ms=50,
        attainment=0.9, max_streams=5, probe_seconds=30, probe_requests=100,
        max_probes=12,
    )  # fmt: skip
    slo = json.loads(flag(cmd, "--metrics")[0])["slo"]
    assert slo == {"ttft_ms": 800, "tpot_ms": 50}
    assert not any("api_key" in a for a in cmd)


def test_poisson_cmd_runs_one_open_loop_rate_with_the_same_slo():
    cmd = poisson_cmd(
        "http://gw/v1", "m", "d", Path("o.json"), rate=2.5, ttft_ms=1000,
        itl_ms=None, seconds=60, api_key="k",
    )  # fmt: skip
    assert flag(cmd, "--profile") == ["kind=poisson,rate=2.5"]
    assert flag(cmd, "--constraint") == ["kind=max_duration,seconds=60"]
    assert json.loads(flag(cmd, "--metrics")[0])["slo"] == {"ttft_ms": 1000}


def test_parse_goodput_reads_real_search_conclusion_and_probe_metrics():
    probes, search = parse_goodput(CONVERGED)
    assert search == {
        "stop_reason": "converged",
        "best_passing_streams": 36,
        "lowest_failing_streams": 38,
    }
    assert [p.users for p in probes] == [1, 2, 4, 8, 16, 32, 64, 48, 40, 36, 38]
    p36 = probes[9]
    assert p36.passed and p36.resolved and not p36.aborted
    assert p36.attainment == pytest.approx(0.99768, abs=1e-4)
    assert p36.attainment_lower < p36.attainment < p36.attainment_upper
    assert p36.determined_requests == 432 and p36.error_rate == 0
    assert p36.request_rate == pytest.approx(213.84, abs=0.01)
    assert p36.tokens_per_min > 0 and p36.output_tok_s > 0
    assert not probes[0].resolved and not probes[6].passed


def test_parse_goodput_without_conclusion_is_a_korean_error():
    with pytest.raises(TtalkkakError, match="탐색 결론"):
        parse_goodput({**CONVERGED, "conclusions": []})


def test_parse_goodput_rejects_probe_benchmark_mismatch():
    r = copy.deepcopy(CONVERGED)
    r["benchmarks"] = r["benchmarks"][:-1]
    with pytest.raises(TtalkkakError, match="맞지 않습니다"):
        parse_goodput(r)


def test_parse_confirm_real_open_loop_run_missed_the_objectives():
    c = parse_confirm(CONFIRM, 0.95)
    assert c["rate_rps"] == 192.5
    assert c["attainment"] == pytest.approx(0.7531, abs=1e-4)
    assert c["attainment_lower"] < c["attainment"] < c["attainment_upper"]
    assert c["determined_requests"] == 1519 and c["error_rate"] == 0
    assert c["held"] is False and c["resolved"] is True


def test_parse_confirm_holds_only_without_errors():
    r = copy.deepcopy(CONFIRM)
    r["benchmarks"][0]["metrics"]["slo_attainment"] = 0.99
    assert parse_confirm(r, 0.95)["held"] is True
    r["benchmarks"][0]["metrics"]["request_totals"]["errored"] = 3
    assert parse_confirm(r, 0.95)["held"] is False


def test_knee_on_real_probes_reports_no_knee_in_korean():
    probes, _ = parse_goodput(CONVERGED)
    k = throughput_knee(probes)
    assert k["status"] == "no_knee" and k["streams"] is None
    assert k["points"] == 11 and k["reason"]


def test_knee_found_on_a_saturating_curve_reuses_probe_throughput():
    probes, _ = parse_goodput(CONVERGED)
    curve = {1: 100, 2: 200, 4: 400, 8: 800, 16: 1000, 32: 1010, 64: 1015}
    shaped = [
        p.model_copy(update={"output_tok_s": curve[p.users]})
        for p in probes
        if p.users in curve
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
    assert any(r["confirm"] and r["confirm"]["held"] is False for r in runs)
    assert all(r["method"] == "goodput" and r["stages"] for r in runs)
    for state, confirmed in (("unconfirmed", False), (True, True)):
        rec = parse_capacity(fc.capacity_json(state, now))[2]
        assert rec.method == "goodput" and rec.confirmed is confirmed


def test_knee_analysis_failure_does_not_fail_the_load_test(monkeypatch):
    from guidellm.benchmark import analysis

    def boom(*a):
        raise ValueError("bad curve")

    monkeypatch.setattr(analysis, "find_throughput_knee", boom)
    probes, _ = parse_goodput(CONVERGED)
    k = throughput_knee(probes)
    assert k["status"] == "error" and "무릎 분석" in k["reason"]
