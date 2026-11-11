import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from ttalkkak.errors import TtalkkakError

MANIFEST = re.compile(r"^\d\d-.*\.yaml$")


def run_kubectl(
    args: list[str], input: str | None = None
) -> subprocess.CompletedProcess:
    if shutil.which("kubectl") is None:
        raise TtalkkakError("kubectl이 필요합니다", 2)
    return subprocess.run(
        ["kubectl", *args], input=input, capture_output=True, text=True, check=False
    )


def manifests(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if MANIFEST.match(p.name))


def _deployments(path: Path) -> list[tuple[str, str]]:
    return [
        (d["metadata"].get("namespace", "default"), d["metadata"]["name"])
        for d in yaml.safe_load_all(path.read_text())
        if d and d.get("kind") == "Deployment"
    ]


def apply_dir(out: Path, kubectl=run_kubectl, wait_s: int = 300) -> list[str]:
    applied = []
    for path in manifests(out):
        r = kubectl(["apply", "-f", str(path), "--server-side"])
        if r.returncode != 0:
            raise TtalkkakError(f"{path.name} 적용 실패: {r.stderr.strip()}")
        applied.append(path.name)
        for ns, name in _deployments(path):
            r = kubectl(
                [
                    "rollout",
                    "status",
                    f"deployment/{name}",
                    "-n",
                    ns,
                    f"--timeout={wait_s}s",
                ]
            )
            if r.returncode != 0:
                d = kubectl(["describe", f"deployment/{name}", "-n", ns]).stdout
                events = d[d.find("Events:") :] if "Events:" in d else d[-1500:]
                raise TtalkkakError(
                    f"{name}이 {wait_s}초 안에 준비되지 않았습니다\n{events.strip()}"
                )
    return applied


def status(ns: str, kubectl=run_kubectl) -> list[dict]:
    r = kubectl(["get", "pods", "-n", ns, "-o", "json"])
    if r.returncode != 0:
        raise TtalkkakError(f"상태를 읽지 못했습니다: {r.stderr.strip()}")
    rows = []
    for p in json.loads(r.stdout).get("items", []):
        cs = p.get("status", {}).get("containerStatuses", [])
        rows.append(
            {
                "name": p["metadata"]["name"],
                "ready": f"{sum(c.get('ready', False) for c in cs)}/{len(cs)}",
                "restarts": sum(c.get("restartCount", 0) for c in cs),
                "phase": p.get("status", {}).get("phase", "?"),
            }
        )
    return rows


def down(out: Path, kubectl=run_kubectl) -> None:
    for path in reversed(manifests(out)):
        kubectl(["delete", "-f", str(path), "--ignore-not-found"])


def gateway_logs(kubectl=run_kubectl) -> list[str]:
    r = kubectl(
        [
            "logs", "-n", "envoy-gateway-system",
            "-l", "gateway.envoyproxy.io/owning-gateway-name=ttalkkak",
            "-c", "envoy", "--tail=-1", "--prefix=false",
        ]
    )  # fmt: skip
    if r.returncode != 0:
        raise TtalkkakError(f"게이트웨이 로그를 읽지 못했습니다: {r.stderr.strip()}")
    return r.stdout.splitlines()
