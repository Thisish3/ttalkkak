import json
import subprocess

import pytest

from ttalkkak.cluster.apply import apply_dir, down, status
from ttalkkak.errors import TtalkkakError

DEP = """apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: ttalkkak}}
"""


class Kubectl:
    def __init__(self, fail_rollout=None, pods=None):
        self.calls, self.fail, self.pods = [], fail_rollout, pods

    def __call__(self, args, input=None):
        self.calls.append(args)
        rc, out = 0, ""
        if args[:1] == ["rollout"] and self.fail and self.fail in args[2]:
            rc = 1
        if args[:1] == ["describe"]:
            out = "Events:\n  Warning  FailedScheduling  0/1 nodes available: insufficient nvidia.com/gpu"
        if args[:2] == ["get", "pods"]:
            out = json.dumps(self.pods)
        return subprocess.CompletedProcess(args, rc, out, "")


def deploy_dir(tmp_path):
    for n, name in (
        ("20-vllm.yaml", "vllm"),
        ("10-redis.yaml", "redis"),
        ("40-gateway.yaml", None),
    ):
        (tmp_path / n).write_text(
            DEP.format(name=name) if name else "kind: Gateway\nmetadata: {name: g}\n"
        )
    (tmp_path / "alerts.yaml").write_text("groups: []\n")
    return tmp_path


def test_apply_in_file_order_and_wait(tmp_path):
    k = Kubectl()
    apply_dir(deploy_dir(tmp_path), kubectl=k, wait_s=30)
    applied = [c[2].rsplit("/", 1)[-1] for c in k.calls if c[0] == "apply"]
    assert applied == ["10-redis.yaml", "20-vllm.yaml", "40-gateway.yaml"]
    rollouts = [c for c in k.calls if c[0] == "rollout"]
    assert [
        "rollout",
        "status",
        "deployment/redis",
        "-n",
        "ttalkkak",
        "--timeout=30s",
    ] in rollouts
    assert (
        k.calls.index(rollouts[0])
        < [i for i, c in enumerate(k.calls) if c[0] == "apply"][1]
    )


def test_apply_stops_with_describe_on_timeout(tmp_path):
    k = Kubectl(fail_rollout="vllm")
    with pytest.raises(TtalkkakError, match="insufficient nvidia.com/gpu"):
        apply_dir(deploy_dir(tmp_path), kubectl=k, wait_s=30)
    assert not any(
        c[0] == "apply" and c[2].endswith("40-gateway.yaml") for c in k.calls
    )


def test_status_table_rows():
    pods = {"items": [{
        "metadata": {"name": "vllm-1"},
        "status": {"phase": "Running", "containerStatuses": [
            {"ready": True, "restartCount": 2}, {"ready": False, "restartCount": 0}]},
    }]}  # fmt: skip
    rows = status("ttalkkak", kubectl=Kubectl(pods=pods))
    assert rows == [
        {"name": "vllm-1", "ready": "1/2", "restarts": 2, "phase": "Running"}
    ]


def test_down_deletes_files_in_reverse(tmp_path):
    k = Kubectl()
    down(deploy_dir(tmp_path), kubectl=k)
    deleted = [c[2].rsplit("/", 1)[-1] for c in k.calls if c[0] == "delete"]
    assert deleted == ["40-gateway.yaml", "20-vllm.yaml", "10-redis.yaml"]


def test_cli_down_requires_yes(tmp_path):
    from typer.testing import CliRunner

    from ttalkkak.cli import app

    r = CliRunner().invoke(
        app, ["ops", "cluster", "down", "--out", str(deploy_dir(tmp_path))]
    )
    assert r.exit_code == 2 and "--yes" in r.output


def test_kubectl_errors_are_reduced_to_the_last_line():
    noisy = (
        "E1111 memcache.go:265 ...\n" * 5 + "The connection to the server was refused\n"
    )

    def k(args, input=None):
        return subprocess.CompletedProcess(args, 1, "", noisy)

    with pytest.raises(TtalkkakError) as e:
        status("ttalkkak", kubectl=k)
    assert str(e.value).endswith("The connection to the server was refused")
    assert "memcache" not in str(e.value)
