import threading
import time

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import Pick, RunState
from ttalkkak.ui.runs import Busy, RunManager


def wait(mgr, run_id, timeout=5):
    end = time.monotonic() + timeout
    while mgr.status(run_id)["running"] and time.monotonic() < end:
        time.sleep(0.01)
    return mgr.status(run_id)


def fake_run(root, release=None, fail=None):
    def run(model, skills, run_id):
        d = root / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(
            RunState(run_id=run_id, done=["probe"]).model_dump_json()
        )
        if release:
            release.wait(5)
        if fail:
            raise fail
        (d / "pick.json").write_text(
            Pick(chosen="c1", base_url="http://h").model_dump_json()
        )
        (d / "report.md").write_text("# report\n")

    return run


def test_start_runs_in_background_and_reports(tmp_path):
    mgr = RunManager(tmp_path, fake_run(tmp_path))
    rid = mgr.start("m", ["code-exec"])
    st = wait(mgr, rid)
    assert st["pick"]["chosen"] == "c1" and st["report_md"] == "# report\n"
    assert st["done"] == ["probe"] and st["error"] is None
    assert mgr.list()[0]["run_id"] == rid


def test_second_start_while_running_is_busy(tmp_path):
    gate = threading.Event()
    mgr = RunManager(tmp_path, fake_run(tmp_path, release=gate))
    rid = mgr.start("m", [])
    with pytest.raises(Busy):
        mgr.start("m", [])
    gate.set()
    assert wait(mgr, rid)["running"] is False


def test_failure_is_recorded_and_next_run_allowed(tmp_path):
    mgr = RunManager(
        tmp_path, fake_run(tmp_path, fail=TtalkkakError("docker 사용 불가", 3))
    )
    rid = mgr.start("m", [])
    st = wait(mgr, rid)
    assert st["error"] == {"message": "docker 사용 불가", "exit_code": 3}
    mgr.run_fn = fake_run(tmp_path)
    assert wait(mgr, mgr.start("m", []))["pick"]["chosen"] == "c1"


def test_unexpected_exception_is_recorded(tmp_path):
    mgr = RunManager(tmp_path, fake_run(tmp_path, fail=RuntimeError("boom")))
    st = wait(mgr, mgr.start("m", []))
    assert st["error"]["message"] == "RuntimeError: boom"


@pytest.mark.parametrize("bad", ["..", "a/b", "", "../x", "a\\b"])
def test_safe_dir_rejects_traversal(tmp_path, bad):
    with pytest.raises(KeyError):
        RunManager(tmp_path, None).safe_dir(bad)


def test_status_of_unknown_run(tmp_path):
    with pytest.raises(KeyError):
        RunManager(tmp_path, None).status("nope")


def test_corrupt_json_does_not_break_status_or_list(tmp_path):
    d = tmp_path / "20260101-000000"
    d.mkdir()
    (d / "state.json").write_text('{"run_id": "x", "do')
    (d / "pick.json").write_text("")
    mgr = RunManager(tmp_path, None)
    st = mgr.status(d.name)
    assert st["done"] == [] and st["pick"] is None
    assert mgr.list()[0]["run_id"] == d.name


def test_shutdown_interrupts_run_so_cleanup_runs(tmp_path):
    cleaned = threading.Event()

    def run(model, skills, run_id):
        try:
            while True:
                time.sleep(0.01)
        finally:
            cleaned.set()

    mgr = RunManager(tmp_path, run)
    mgr.start("m", [])
    time.sleep(0.05)
    mgr.shutdown(timeout=5)
    assert cleaned.is_set()


def test_worker_thread_is_not_daemon(tmp_path):
    gate = threading.Event()
    mgr = RunManager(tmp_path, lambda m, s, r: gate.wait(5))
    mgr.start("m", [])
    assert mgr._thread is not None and not mgr._thread.daemon
    gate.set()
