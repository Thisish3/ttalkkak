import socket
import subprocess
import time

import httpx

from ttalkkak.engines.base import EngineAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.platform import Platform
from ttalkkak.schemas import Candidate, FailureKind, Launched

_PATTERNS: list[tuple[FailureKind, tuple[str, ...]]] = [
    ("oom", ("out of memory", "no available memory for the cache blocks")),
    ("bad_arg", ("unrecognized arguments", "invalid choice", "error: argument")),
    (
        "download",
        (
            "hfhubhttperror",
            "repositorynotfound",
            "gatedrepo",
            "401 client error",
            "403 client error",
            "unauthorized",
            "pull access denied",
        ),
    ),
]
PULL_TIMEOUT_S = 3600
NVIDIA = Platform(vendor="nvidia")


def check_docker(run=subprocess.run) -> None:
    try:
        run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as err:
        raise TtalkkakError(
            "docker 사용 불가: Docker를 설치하고 데몬을 켜 주세요", 3
        ) from err


def free_port(preferred: int = 8000) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise TtalkkakError("빈 포트를 찾지 못했습니다", 3)


def classify_failure(logs: str, timed_out: bool) -> FailureKind:
    low = logs.lower()
    for kind, needles in _PATTERNS:
        if any(n in low for n in needles):
            return kind
    return "timeout" if timed_out else "unknown"


def _out(run, cmd: list[str]) -> str:
    return run(cmd, capture_output=True, text=True, timeout=60).stdout


def stop(container: str, run=subprocess.run) -> None:
    run(["docker", "rm", "-f", container], capture_output=True, text=True, timeout=60)


def _failed(
    c: Candidate,
    logs: str,
    timed_out: bool,
    cid: str | None = None,
    kind: FailureKind | None = None,
) -> Launched:
    return Launched(
        candidate_id=c.id,
        status="failed",
        container=cid,
        failure=kind or classify_failure(logs, timed_out),
        log_tail=logs.splitlines()[-50:],
        logs=logs,
    )


def launch(
    c: Candidate,
    adapter: EngineAdapter,
    platform: Platform = NVIDIA,
    run=subprocess.run,
    client: httpx.Client | None = None,
    wait_s: int = 900,
    sleep=time.sleep,
) -> Launched:
    client = client or httpx.Client()
    spec = adapter.build_args(c)
    image = platform.image(adapter.name)
    if image is None:
        return _failed(
            c,
            f"no image for {adapter.name} on {platform.vendor}",
            False,
            kind="bad_arg",
        )
    try:
        pulled = run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=PULL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _failed(c, f"docker pull {image} timed out", True, kind="download")
    if pulled.returncode != 0:
        return _failed(c, pulled.stderr or "", False, kind="download")
    port = free_port()
    cmd = [
        "docker",
        "run",
        "-d",
        *platform.gpu_flags(),
        "--ipc=host",
        "-p",
        f"{port}:{adapter.container_port}",
        "-v",
        "ttalkkak-hf-cache:/root/.cache/huggingface",
        "-e",
        "HF_TOKEN",
        *[f"-e{k}={v}" for k, v in spec.env.items()],
        image,
        *spec.args,
    ]
    started = run(cmd, capture_output=True, text=True, timeout=120)
    cid = (started.stdout or "").strip()
    if started.returncode != 0 or not cid:
        return _failed(c, (started.stderr or "") + (started.stdout or ""), False)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline, timed_out = time.monotonic() + wait_s, True
        while time.monotonic() < deadline:
            if adapter.health(base, client):
                timed_out = False
                break
            state = _out(run, ["docker", "inspect", "-f", "{{.State.Status}}", cid])
            if state.strip() != "running":
                timed_out = False
                break
            sleep(5)
        got = run(["docker", "logs", cid], capture_output=True, text=True, timeout=60)
        logs = (got.stdout or "") + (got.stderr or "")
        if adapter.health(base, client):
            return Launched(
                candidate_id=c.id,
                status="ready",
                base_url=base,
                container=cid,
                logs=logs,
                collected=adapter.collect(base, client),
            )
    except BaseException:
        stop(cid, run)
        raise
    stop(cid, run)
    return _failed(c, logs, timed_out, cid)
