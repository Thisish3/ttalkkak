"""Autoscale loop of `ops serve` (design 4.6): sample, decide, scale, log."""

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from ttalkkak.autoscale import Decision, Rules, Sample, State, decide
from ttalkkak.capacity import (
    idle_replicas,
    parse_capacity,
    record_for,
    utilization,
)
from ttalkkak.live import prom as q
from ttalkkak.ops_api.sources import SA_DIR

API = "https://kubernetes.default.svc"
PREFIX = "AUTOSCALE "
INTERVAL_S = 10
Transport = Callable[..., dict]


def in_cluster_transport(
    api: str = API,
    sa_dir: Path | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> Transport:
    """Transport that talks to the API server with the pod's service account.

    The token is read on every request (the kubelet rotates it) and the client
    is built on first use, so a missing token only fails the call.
    """
    sa_dir = sa_dir or SA_DIR
    holder: dict[str, httpx.Client] = {}

    def client() -> httpx.Client:
        if "c" not in holder:
            holder["c"] = httpx.Client(
                base_url=api,
                verify=str(sa_dir / "ca.crt"),
                timeout=10,
                transport=http_transport,
            )
        return holder["c"]

    def call(method: str, path: str, body: dict | None = None) -> dict:
        token = (sa_dir / "token").read_text().strip()
        headers = {"authorization": f"Bearer {token}"}
        if body is not None:
            headers["content-type"] = "application/merge-patch+json"
        r = client().request(
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
        # Scale 하위 리소스(autoscaling/v1)의 status에는 readyReplicas가 없다
        # (replicas, selector뿐). 원하는 수와 준비된 수는 Deployment 본체에서 읽는다.
        body = self._call(
            "GET", f"/apis/apps/v1/namespaces/{ns}/deployments/{deploy}", None
        )
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
    last_desired: int | None = None
    pending_up: tuple[datetime, int] | None = None


class ReadyEvent(BaseModel):
    """Follow-up of an up decision: the new replicas became ready."""

    t: datetime
    action: Literal["ready"] = "ready"
    from_replicas: int
    to_replicas: int
    reason: str
    ready_after_s: int


_COOLDOWN_SECONDS = re.compile(r"\d+초")


def hold_key(reason: str) -> str:
    """Reason without elapsed-time numbers, so a cooldown logs one line."""
    return _COOLDOWN_SECONDS.sub("#초", reason)


def _emit(event: BaseModel) -> None:
    print(PREFIX + event.model_dump_json(), flush=True)


def _sample(
    k8s, prom, auto: dict, ns: str, now: datetime, desired: int, ready: int
) -> tuple[Sample, str | None]:
    rows = prom.instant(q.RUNNING)
    model = next((m["model_name"] for m, _ in rows if m.get("model_name")), "")
    tpm = prom.instant(q.TPM)
    waiting = int(sum(v for _, v in prom.instant(q.WAITING)))
    cap = k8s.get_configmap(ns, "ttalkkak-capacity").get("capacity.json")
    record = record_for(parse_capacity(cap, ns), desired)
    pct = None
    if tpm and model and record is not None:
        pct = utilization(tpm[0][1], record, desired, model)["pct"]
    running = {m["pod"]: s for m, s in prom.range(q.RUNNING, 5, 30) if m.get("pod")}
    idle = len(idle_replicas(running, now, 5, 30))
    sample = Sample(
        t=now, util_pct=pct, waiting=waiting, ready=ready, desired=desired,
        idle_replicas=idle,
    )  # fmt: skip
    if (
        record is None
        or not record.max_tokens_per_min
        or (model and record.model != model)
    ):
        return sample, "missing"
    return sample, "unconfirmed" if record.confirmed is False else None


def tick(
    k8s, prom, state: State, rules: Rules, now: datetime, ns: str
) -> tuple[Decision | None, State]:
    """One loop step. None when autoscale is off; errors become a hold."""
    st = state if isinstance(state, LoopState) else LoopState(**state.model_dump())
    known = st.last_desired
    pending = st.pending_up
    ready_event = None
    try:
        auto = k8s.get_configmap(ns, "ttalkkak-autoscale")
        if auto.get("enabled") != "true":
            return None, st
        max_r = int(auto["max_replicas"])
        deploy = auto["deployment"]
        desired, ready = k8s.get_scale(ns, deploy)
        known = desired
        sample, record_issue = _sample(k8s, prom, auto, ns, now, desired, ready)
        decision, new = decide(st, sample, rules, max_r=max_r)
        if decision.action == "hold" and sample.util_pct is None:
            reason = {
                "missing": f"용량 기록 없음: 복제본 {desired}개 부하 시험 필요",
                "unconfirmed": (
                    f"용량 기록 확인 실패: 복제본 {desired}개 부하 시험 다시 필요"
                ),
            }.get(record_issue, "지표 없음 (요청량 또는 모델 지표를 읽지 못했습니다)")
            decision = decision.model_copy(update={"reason": reason})
        if decision.action != "hold":
            k8s.set_scale(ns, deploy, decision.to_replicas)
            pending = (
                (decision.t, decision.to_replicas) if decision.action == "up" else None
            )
        elif pending is not None:
            if desired != pending[1]:
                pending = None
            elif ready >= desired:
                secs = round((now - pending[0]).total_seconds())
                ready_event = ReadyEvent(
                    t=now, from_replicas=desired, to_replicas=desired,
                    reason=f"새 복제본 준비 완료 (복제본 {desired}개)",
                    ready_after_s=secs,
                )  # fmt: skip
                pending = None
    except Exception as err:  # noqa: BLE001 - the loop must survive any failure
        decision = Decision(
            t=now, action="hold", from_replicas=known, to_replicas=known,
            reason=f"오류: {err}",
        )  # fmt: skip
        new = st
    new = LoopState(**new.model_dump())
    new.last_desired, new.pending_up = known, pending
    key = hold_key(decision.reason)
    if decision.action != "hold":
        _emit(decision)
        new.last_logged_hold = None
    elif key != st.last_logged_hold:
        _emit(decision)
        new.last_logged_hold = key
    else:
        new.last_logged_hold = st.last_logged_hold
    if ready_event is not None:
        _emit(ready_event)
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


def start_loop(
    k8s, prom, ns: str, stop: threading.Event | None = None
) -> threading.Thread:
    t = threading.Thread(target=run_loop, args=(k8s, prom, ns, None, stop), daemon=True)
    t.start()
    return t


def parse_decisions(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        i = line.find(PREFIX)
        if i < 0:
            continue
        raw = line[i + len(PREFIX) :]
        for model in (Decision, ReadyEvent):
            try:
                out.append(json.loads(model.model_validate_json(raw).model_dump_json()))
                break
            except ValueError:
                continue
    return out
