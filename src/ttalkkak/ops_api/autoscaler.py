"""Autoscale loop of `ops serve` (design 4.6): sample, decide, scale, log."""

import json
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import httpx

from ttalkkak.autoscale import Decision, Rules, Sample, State, decide
from ttalkkak.capacity import CapacityRecord, idle_replicas, utilization
from ttalkkak.live import prom as q
from ttalkkak.ops_api.sources import SA_DIR

API = "https://kubernetes.default.svc"
PREFIX = "AUTOSCALE "
INTERVAL_S = 10
Transport = Callable[..., dict]


def in_cluster_transport(api: str = API) -> Transport:
    """Transport that talks to the API server with the pod's service account."""
    http = httpx.Client(
        base_url=api,
        verify=str(SA_DIR / "ca.crt"),
        headers={"authorization": f"Bearer {(SA_DIR / 'token').read_text().strip()}"},
        timeout=10,
    )

    def call(method: str, path: str, body: dict | None = None) -> dict:
        headers = {"content-type": "application/merge-patch+json"} if body else {}
        r = http.request(
            method,
            path,
            content=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()

    return call


class K8s:
    def __init__(self, transport: Transport | None = None):
        self._call = transport or in_cluster_transport()

    def get_configmap(self, ns: str, name: str) -> dict:
        body = self._call("GET", f"/api/v1/namespaces/{ns}/configmaps/{name}", None)
        return body.get("data") or {}

    def get_scale(self, ns: str, deploy: str) -> tuple[int, int]:
        body = self._call("GET", _scale_path(ns, deploy), None)
        return (
            int(body.get("spec", {}).get("replicas") or 0),
            int(body.get("status", {}).get("readyReplicas") or 0),
        )

    def set_scale(self, ns: str, deploy: str, n: int) -> None:
        self._call("PATCH", _scale_path(ns, deploy), {"spec": {"replicas": n}})


def _scale_path(ns: str, deploy: str) -> str:
    return f"/apis/apps/v1/namespaces/{ns}/deployments/{deploy}/scale"


class LoopState(State):
    last_logged_hold: str | None = None


def _emit(decision: Decision) -> None:
    print(PREFIX + decision.model_dump_json(), flush=True)


def _sample(k8s, prom, auto: dict, ns: str, now: datetime) -> tuple[Sample, str]:
    deploy = auto["deployment"]
    desired, ready = k8s.get_scale(ns, deploy)
    rows = prom.instant(q.RUNNING)
    model = next((m["model_name"] for m, _ in rows if m.get("model_name")), "")
    tpm = prom.instant(q.TPM)
    waiting = int(sum(v for _, v in prom.instant(q.WAITING)))
    raw = json.loads(
        k8s.get_configmap(ns, "ttalkkak-capacity").get("capacity.json") or "{}"
    )
    record = CapacityRecord.model_validate(raw) if raw else None
    pct = None
    if tpm and model:
        pct = utilization(tpm[0][1], record, desired, model)["pct"]
    running = {m["pod"]: s for m, s in prom.range(q.RUNNING, 5, 30) if m.get("pod")}
    idle = len(idle_replicas(running, now, 5, 30))
    sample = Sample(
        t=now, util_pct=pct, waiting=waiting, ready=ready, desired=desired,
        idle_replicas=idle,
    )  # fmt: skip
    return sample, deploy


def tick(
    k8s, prom, state: State, rules: Rules, now: datetime, ns: str
) -> tuple[Decision | None, State]:
    """One loop step. None when autoscale is off; errors become a hold."""
    st = state if isinstance(state, LoopState) else LoopState(**state.model_dump())
    desired = 0
    try:
        auto = k8s.get_configmap(ns, "ttalkkak-autoscale")
        if auto.get("enabled") != "true":
            return None, st
        max_r = int(auto["max_replicas"])
        sample, deploy = _sample(k8s, prom, auto, ns, now)
        desired = sample.desired
        decision, new = decide(st, sample, rules, max_r=max_r)
        if decision.action != "hold":
            k8s.set_scale(ns, deploy, decision.to_replicas)
    except Exception as err:  # noqa: BLE001 - the loop must survive any failure
        decision = Decision(
            t=now, action="hold", from_replicas=desired, to_replicas=desired,
            reason=f"오류: {err}",
        )  # fmt: skip
        new = st
    new = LoopState(**new.model_dump())
    if decision.action != "hold":
        _emit(decision)
        new.last_logged_hold = None
    elif decision.reason != st.last_logged_hold:
        _emit(decision)
        new.last_logged_hold = decision.reason
    else:
        new.last_logged_hold = st.last_logged_hold
    return decision, new


def run_loop(
    k8s, prom, ns: str, rules: Rules | None = None, stop: threading.Event | None = None
) -> None:
    rules, stop, state = rules or Rules(), stop or threading.Event(), State()
    while not stop.is_set():
        started = time.monotonic()
        try:
            _, state = tick(k8s, prom, state, rules, datetime.now(UTC), ns)
        except Exception as err:  # noqa: BLE001
            print(f"autoscale 루프 오류: {err}", flush=True)
        stop.wait(max(0.0, INTERVAL_S - (time.monotonic() - started)))


def start_loop(k8s, prom, ns: str) -> threading.Thread:
    t = threading.Thread(target=run_loop, args=(k8s, prom, ns), daemon=True)
    t.start()
    return t


def parse_decisions(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        i = line.find(PREFIX)
        if i < 0:
            continue
        try:
            d = Decision.model_validate_json(line[i + len(PREFIX) :])
        except ValueError:
            continue
        out.append(json.loads(d.model_dump_json()))
    return out
