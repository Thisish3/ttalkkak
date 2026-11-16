"""Live operator snapshot: Prometheus engine metrics plus gateway access logs."""

from datetime import datetime

from ttalkkak.billing.bill import read_log
from ttalkkak.capacity import (
    CapacityRecord,
    cache_hit_rate,
    idle_replicas,
    period_summary,
    tenant_usage,
    utilization,
)
from ttalkkak.chips import ChipConfig
from ttalkkak.cost.model import CostConfig
from ttalkkak.live import prom as q
from ttalkkak.live.prom import Prom, PromError

KV_SPREAD_PP = 30.0
TENANT_LIMIT_PCT = 95.0
EMPTY = "Prometheus에서 값을 받지 못했습니다"


class ShortCircuit:
    """Wraps a Prom for one snapshot: after the first PromError, later queries
    re-raise it without another request."""

    def __init__(self, prom):
        self.prom, self.error = prom, None

    def _call(self, fn, *args):
        if self.error is not None:
            raise self.error
        try:
            return fn(*args)
        except PromError as err:
            self.error = err
            raise

    def instant(self, query):
        return self._call(self.prom.instant, query)

    def range(self, query, minutes, step_s):
        return self._call(self.prom.range, query, minutes, step_s)


def _sum(rows: list[tuple[dict, float]]) -> float | None:
    return sum(v for _, v in rows) if rows else None


def _first(rows: list[tuple[dict, float]]) -> float | None:
    return rows[0][1] if rows else None


def _by_pod(rows: list[tuple[dict, float]]) -> dict[str, float]:
    return {m["pod"]: v for m, v in rows if m.get("pod")}


def _headline(prom: Prom, chip: ChipConfig, cost: CostConfig | None, errors: dict):
    out = dict.fromkeys(
        ("concurrent", "ttft_p99_ms", "itl_p99_ms", "output_tok_s", "power_w"), None
    )
    out["krw_per_hour"] = None
    try:
        out["concurrent"] = _sum(prom.instant(q.RUNNING))
        ttft = _first(prom.instant(q.TTFT_P99))
        itl = _first(prom.instant(q.ITL_P99))
        out["ttft_p99_ms"] = None if ttft is None else ttft * 1000
        out["itl_p99_ms"] = None if itl is None else itl * 1000
        out["output_tok_s"] = _sum(prom.instant(q.OUTPUT_TPS))
        power_metric = chip.telemetry.metrics.get("power")
        if power_metric:
            out["power_w"] = _sum(prom.instant(f"sum({power_metric})"))
        else:
            errors["power_w"] = "이 칩은 전력 지표를 제공하지 않습니다"
    except PromError as err:
        errors["headline"] = str(err)
        return out
    if out["concurrent"] is None and out["output_tok_s"] is None:
        errors["headline"] = EMPTY
    if out["power_w"] is not None and cost is not None:
        out["krw_per_hour"] = (
            out["power_w"] * cost.electricity_krw_per_kwh * cost.pue / 3.6e6 * 3600
        )
    return out


def _capacity(prom, rec, replicas, model, errors):
    base = utilization(0.0, rec, replicas, model)
    base["period"] = period_summary([])
    base["series"] = []
    try:
        now_rows = prom.instant(q.TPM)
        if not now_rows:
            errors["capacity"] = EMPTY
            return base
        cap = utilization(now_rows[0][1], rec, replicas, model)
        series = []
        if cap["max_tpm"]:
            for _, s in prom.range(q.TPM, 60, 30):
                series += [(t, v / cap["max_tpm"] * 100) for t, v in s]
        cap["period"] = period_summary(series)
        cap["series"] = [[t.timestamp(), v] for t, v in sorted(series)]
        return cap
    except PromError as err:
        errors["capacity"] = str(err)
        return base


def _signals(prom, tenants, pods_ready, max_replicas, now, errors) -> dict:
    sig = {
        "rejected_429_1h": sum(t["throttled_1h"] for t in tenants),
        "waiting": None,
        "idle_replicas": None,
        "ready_replicas": pods_ready,
        "max_replicas": max_replicas,
    }
    try:
        sig["waiting"] = _sum(prom.instant(q.WAITING))
        running = {m["pod"]: s for m, s in prom.range(q.RUNNING, 5, 30) if m.get("pod")}
        sig["idle_replicas"] = len(idle_replicas(running, now, 5, 30))
    except PromError as err:
        errors.setdefault("signals", str(err))
    return sig


def _replicas(prom, pods, chip, errors) -> list[dict]:
    rows = [
        {
            "pod": p["name"],
            "status": p.get("phase", "?"),
            "running": None,
            "waiting": None,
            "kv": None,
            "hit_rate": None,
            "gpu_util": None,
        }
        for p in pods
    ]
    try:
        running = _by_pod(prom.instant(q.RUNNING))
        waiting = _by_pod(prom.instant(q.WAITING))
        kv = _by_pod(prom.instant(q.KV))
        prompt = _by_pod(prom.instant(q.PROMPT))
        cached = _by_pod(prom.instant(q.CACHED))
        util_metric = chip.telemetry.metrics.get("utilization")
        gpu = _by_pod(prom.instant(util_metric)) if util_metric else {}
    except PromError as err:
        errors["replicas"] = str(err)
        return rows
    for r in rows:
        n = r["pod"]
        r["running"], r["waiting"] = running.get(n), waiting.get(n)
        r["kv"] = kv[n] * 100 if n in kv else None
        r["hit_rate"] = (
            cache_hit_rate(prompt[n], cached.get(n, 0.0)) if n in prompt else None
        )
        r["gpu_util"] = gpu.get(n)
    return rows


def _recommend(replicas: list[dict], tenants: list[dict]) -> list[dict]:
    out = []
    kvs = [r["kv"] for r in replicas if r["kv"] is not None]
    if len(kvs) >= 2 and max(kvs) - min(kvs) >= KV_SPREAD_PP:
        out.append(
            {
                "kind": "router_weights",
                "text": "한 복제본에 몰립니다. 라우터의 대기열·KV 가중치를 올리세요",
            }
        )
    for t in tenants:
        if t["pct"] is not None and t["pct"] >= TENANT_LIMIT_PCT:
            out.append(
                {
                    "kind": "tenant_limit",
                    "tenant": t["tenant"],
                    "text": f"{t['tenant']} 고객이 한도를 다 쓰고 있습니다",
                }
            )
    return out


def build_snapshot(
    prom: Prom,
    pods: list[dict],
    logs: list[str],
    limits: dict,
    capacity: CapacityRecord | None,
    chip: ChipConfig,
    model: str,
    now: datetime,
    autoscale: dict,
    cost: CostConfig | None = None,
    missing_loadtest: list[int] | None = None,
    missing_text: str | None = None,
) -> dict:
    errors: dict[str, str] = {}
    if not isinstance(prom, ShortCircuit):
        prom = ShortCircuit(prom)
    try:
        tenants = tenant_usage(read_log(logs), limits, now)
    except Exception as err:  # noqa: BLE001 - one bad section must not fail the rest
        tenants, errors["tenants"] = [], f"게이트웨이 로그를 읽지 못했습니다: {err}"
    ready = sum(
        1 for p in pods if p.get("phase") == "Running" and _all_ready(p.get("ready"))
    )
    max_replicas = autoscale.get("max_replicas") or len(pods)
    cap = _capacity(prom, capacity, len(pods), model, errors)
    cap["missing_loadtest"] = missing_loadtest or []
    cap["missing_text"] = missing_text
    cap["signals"] = _signals(prom, tenants, ready, max_replicas, now, errors)
    replicas = _replicas(prom, pods, chip, errors)
    return {
        "headline": _headline(prom, chip, cost, errors),
        "capacity": cap,
        "tenants": tenants,
        "replicas": replicas,
        "recommendations": _recommend(replicas, tenants),
        "autoscale": {
            "enabled": autoscale.get("enabled"),
            "decisions": list(autoscale.get("decisions") or [])[-10:],
        },
        "errors": errors,
    }


def _all_ready(ready: str | None) -> bool:
    try:
        a, b = (ready or "").split("/")
        return int(b) > 0 and a == b
    except ValueError:
        return False
