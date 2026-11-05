import socket
import subprocess

import httpx
import pytest

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.launch.docker import check_docker, classify_failure, free_port, launch
from ttalkkak.schemas import Candidate

C = Candidate(id="c1", engine="vllm", model_id="m/x", args={"max_model_len": 4096})


def test_docker_missing_is_clear_error():
    def run(*a, **k):
        raise FileNotFoundError("docker")

    with pytest.raises(TtalkkakError) as e:
        check_docker(run)
    assert e.value.exit_code == 3 and "docker" in str(e.value)


def test_free_port_skips_busy_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    busy = s.getsockname()[1]
    try:
        assert free_port(busy) != busy
    finally:
        s.close()


@pytest.mark.parametrize(
    "log,kind",
    [
        ("torch.OutOfMemoryError: CUDA out of memory", "oom"),
        ("ValueError: No available memory for the cache blocks", "oom"),
        ("api_server.py: error: unrecognized arguments: --foo", "bad_arg"),
        ("huggingface_hub.errors.HfHubHTTPError: 401", "download"),
        ("something odd", "unknown"),
    ],
)
def test_classify_failure(log, kind):
    assert classify_failure(log, timed_out=False) == kind


def test_classify_timeout_wins_when_no_signal():
    assert classify_failure("still loading", timed_out=True) == "timeout"


class FakeDocker:
    def __init__(self, exit_early=False):
        self.calls, self.exit_early = [], exit_early

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[:2] == ["docker", "run"]:
            out = "cid123\n"
        elif cmd[:2] == ["docker", "logs"]:
            out = (
                "CUDA out of memory" if self.exit_early else "Using max model len 4096"
            )
        elif cmd[:2] == ["docker", "inspect"]:
            out = "exited" if self.exit_early else "running"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _client(status):
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "m/x", "max_model_len": 4096}]}
            )
        if req.url.path == "/metrics":
            return httpx.Response(200, text="")
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_launch_ready_collects_logs_and_models():
    fake = FakeDocker()
    r = launch(
        C, VllmAdapter(), run=fake, client=_client(200), wait_s=5, sleep=lambda s: None
    )
    assert r.status == "ready" and r.container == "cid123"
    assert r.models_json["data"][0]["max_model_len"] == 4096
    assert fake.calls[0][:2] == ["docker", "pull"]
    run_cmd = next(c for c in fake.calls if c[:2] == ["docker", "run"])
    assert "--gpus" in run_cmd and "vllm/vllm-openai:v0.33.0" in run_cmd


def test_launch_failed_when_container_exits():
    fake = FakeDocker(exit_early=True)
    r = launch(
        C, VllmAdapter(), run=fake, client=_client(503), wait_s=5, sleep=lambda s: None
    )
    assert r.status == "failed" and r.failure == "oom"
    assert ["docker", "rm", "-f", "cid123"] in fake.calls


def test_failure_logs_on_stderr_are_read():
    class StderrDocker(FakeDocker):
        def __call__(self, cmd, **kw):
            r = super().__call__(cmd, **kw)
            if cmd[:2] == ["docker", "logs"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="", stderr="CUDA out of memory"
                )
            return r

    r = launch(
        C,
        VllmAdapter(),
        run=StderrDocker(exit_early=True),
        client=_client(503),
        wait_s=5,
        sleep=lambda s: None,
    )
    assert r.failure == "oom"


def test_pull_timeout_is_a_download_failure_not_a_traceback():
    class SlowPull(FakeDocker):
        def __call__(self, cmd, **kw):
            if cmd[:2] == ["docker", "pull"]:
                raise subprocess.TimeoutExpired(cmd, 1)
            return super().__call__(cmd, **kw)

    fake = SlowPull()
    r = launch(
        C, VllmAdapter(), run=fake, client=_client(200), wait_s=5, sleep=lambda s: None
    )
    assert r.status == "failed" and r.failure == "download"
    assert not any(c[:2] == ["docker", "run"] for c in fake.calls)


def test_interrupt_while_waiting_removes_container():
    fake = FakeDocker()

    def sleep(s):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        launch(C, VllmAdapter(), run=fake, client=_client(503), wait_s=5, sleep=sleep)
    assert ["docker", "rm", "-f", "cid123"] in fake.calls


def test_failed_docker_run_keeps_stderr():
    class RunFails(FakeDocker):
        def __call__(self, cmd, **kw):
            if cmd[:2] == ["docker", "run"]:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(
                    cmd,
                    125,
                    stdout="",
                    stderr="Bind for 0.0.0.0:8000 failed: port is already allocated",
                )
            return super().__call__(cmd, **kw)

    fake = RunFails()
    r = launch(
        C, VllmAdapter(), run=fake, client=_client(503), wait_s=5, sleep=lambda s: None
    )
    assert r.status == "failed" and "port is already allocated" in r.logs
    assert not any(
        c[:2] in (["docker", "inspect"], ["docker", "logs"]) for c in fake.calls
    )


def test_free_port_checks_all_interfaces(monkeypatch):
    seen = []

    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            seen.append(addr)

    monkeypatch.setattr("ttalkkak.launch.docker.socket.socket", lambda: Sock())
    free_port(8000)
    assert seen[0] == ("0.0.0.0", 8000)


@pytest.mark.parametrize(
    "log",
    [
        "INFO 11-05 Avg prompt throughput: 401.3 tokens/s, pid 403",
        "Loading weights took 4.01 s",
    ],
)
def test_numbers_alone_are_not_download_failures(log):
    assert classify_failure(log, timed_out=False) == "unknown"


def test_http_401_client_error_is_download():
    log = "requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url"
    assert classify_failure(log, timed_out=False) == "download"
