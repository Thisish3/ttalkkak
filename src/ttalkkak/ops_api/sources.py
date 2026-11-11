"""Where ops serve reads tenants, JWKS and gateway access logs from."""

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
ENVOY_NS = "envoy-gateway-system"
ENVOY_SELECTOR = "gateway.envoyproxy.io/owning-gateway-name=ttalkkak"


def cached(fn: Callable[[], object], ttl_s: float, now=time.monotonic):
    state: dict = {}

    def get():
        if "v" not in state or now() - state["t"] > ttl_s:
            state["v"], state["t"] = fn(), now()
        return state["v"]

    return get


def json_file(path: Path) -> Callable[[], dict]:
    return cached(lambda: json.loads(path.read_text(encoding="utf-8")), 10)


def jwks_url(url: str, http: httpx.Client) -> Callable[[], dict]:
    def fetch() -> dict:
        r = http.get(url)
        r.raise_for_status()
        return r.json()

    return cached(fetch, 300)


def log_files(path: Path) -> Callable[[], Iterable[str]]:
    def read() -> list[str]:
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        return [ln for f in files if f.is_file() for ln in f.read_text().splitlines()]

    return read


def cluster_logs(
    http: httpx.Client | None = None,
    api: str = "https://kubernetes.default.svc",
    namespace: str = ENVOY_NS,
    selector: str = ENVOY_SELECTOR,
) -> Callable[[], list[str]]:
    """Envoy access logs of the ttalkkak gateway pods via the Kubernetes API.

    Logs vanish when an Envoy pod restarts; counters then reset (Prometheus
    handles that) and bills built from the cluster miss the lost part.
    """
    if http is None:
        token = (SA_DIR / "token").read_text().strip()
        http = httpx.Client(
            base_url=api,
            verify=str(SA_DIR / "ca.crt"),
            headers={"authorization": f"Bearer {token}"},
            timeout=30,
        )

    def fetch() -> list[str]:
        pods = http.get(
            f"/api/v1/namespaces/{namespace}/pods", params={"labelSelector": selector}
        )
        pods.raise_for_status()
        lines: list[str] = []
        for p in pods.json().get("items", []):
            name = p["metadata"]["name"]
            r = http.get(
                f"/api/v1/namespaces/{namespace}/pods/{name}/log",
                params={"container": "envoy"},
            )
            if r.status_code == 200:
                lines += r.text.splitlines()
        return lines

    return cached(fetch, 15)
