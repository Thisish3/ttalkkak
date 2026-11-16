import copy
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttalkkak.capacity import LOADTEST_TENANT
from ttalkkak.errors import TtalkkakError
from ttalkkak.live.loadtest import LoadTestRunner, stages_for
from ttalkkak.tenants.store import TenantStore

BASE = json.loads(
    (Path(__file__).parent / "fixtures/guidellm/report_mock.json").read_text()
)


def report_for(users, errored=0):
    r = copy.deepcopy(BASE)
    b = r["benchmarks"][0]
    r["benchmarks"] = [b]
    b["config"]["strategy"] = {"type_": "concurrent", "streams": users}
    b["duration"] = 15.0
    m = b["metrics"]
    m["time_to_first_token_ms"]["successful"]["percentiles"]["p99"] = 100.0 * users
    m["inter_token_latency_ms"]["successful"]["percentiles"]["p99"] = 20.0
    m["output_tokens_per_second"]["successful"]["mean"] = 50.0 * users
    m["prompt_token_count"]["successful"]["total_sum"] = 2500 * users
    m["output_token_count"]["successful"]["total_sum"] = 500 * users
    m["request_totals"] = {
        "successful": 10 - errored, "errored": errored, "incomplete": 0, "total": 10
    }  # fmt: skip
    return r


def out_path(cmd):
    return Path(
        next(a.split("path=")[1] for a in cmd if a.startswith("kind=json,path="))
    )


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


def good_run(cmd, **kw):
    users = int(cmd[cmd.index("profile.streams") + 1])
    out_path(cmd).write_text(json.dumps(report_for(users)))


def test_stages_for():
    assert stages_for(20) == [1, 5, 10, 20]
    assert stages_for(7) == [1, 5, 7]
    assert stages_for(50) == [1, 5, 10, 20, 50]


@pytest.mark.parametrize("n", [0, -1, 101])
def test_stages_for_out_of_range(n):
    with pytest.raises(ValueError):
        stages_for(n)


def test_runner_writes_record_and_capacity(tmp_path, store):
    k = Kubectl()
    r = runner(tmp_path, store, good_run, k)
    tid = r.start(10, replicas=2, autoscale_on=False)
    rec = json.loads((tmp_path / "runs/loadtests" / f"{tid}.json").read_text())
    assert [s["users"] for s in rec["stages"]] == [1, 5, 10]
    assert rec["replicas"] == 2 and rec["error"] is None
    assert rec["capacity_tpm"] == pytest.approx(3000 * 10 / 15 * 60)
    ((args, manifest),) = k.calls
    assert args == ["apply", "-f", "-"]
    cm = json.loads(manifest)
    assert cm["metadata"]["name"] == "ttalkkak-capacity"
    cap = json.loads(cm["data"]["capacity.json"])
    assert cap["loadtest_id"] == tid and cap["replicas"] == 2
    assert r.list()[0]["id"] == tid


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
