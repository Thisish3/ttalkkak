import json
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel

LOADTEST_TENANT = "ttalkkak-loadtest"


class Stage(BaseModel):
    users: int
    ttft_p99_ms: float
    itl_p99_ms: float
    output_tok_s: float
    tokens_per_min: float
    error_rate: float


class CapacityRecord(BaseModel):
    loadtest_id: str
    created_at: datetime
    replicas: int
    model: str
    ttft_target_ms: float
    max_tokens_per_min: float | None


def parse_capacity(raw: str | None) -> dict[int, CapacityRecord]:
    """Records by replica count from the ConfigMap value.

    New format: {"<replicas>": record, ...}. The old single record is read as
    the entry for its own replica count.
    """
    data = json.loads(raw) if raw else {}
    if not data:
        return {}
    if "replicas" in data:
        rec = CapacityRecord.model_validate(data)
        return {rec.replicas: rec}
    return {int(n): CapacityRecord.model_validate(r) for n, r in data.items()}


def merge_capacity(raw: str | None, rec: CapacityRecord) -> str:
    """ConfigMap value with rec replacing only the entry for its replica count."""
    records = {**parse_capacity(raw), rec.replicas: rec}
    return json.dumps(
        {str(n): json.loads(r.model_dump_json()) for n, r in sorted(records.items())}
    )


def record_for(records: dict[int, CapacityRecord], replicas: int):
    return records.get(replicas)


def capacity_counts(records: dict[int, CapacityRecord]) -> list[int]:
    return sorted(records)


def missing_loadtest_counts(
    records: dict[int, CapacityRecord], max_replicas: int
) -> list[int]:
    return [n for n in range(1, max_replicas + 1) if n not in records]


def missing_loadtest_text(
    records: dict[int, CapacityRecord], max_replicas: int
) -> str | None:
    missing = missing_loadtest_counts(records, max_replicas)
    if not missing:
        return None
    return "부하 시험 필요: " + ", ".join(f"{n}개" for n in missing)


def capacity_from_stages(stages: list[Stage], target_ms: float) -> float | None:
    ok = [s for s in stages if s.ttft_p99_ms <= target_ms and s.error_rate == 0]
    return max(ok, key=lambda s: s.users).tokens_per_min if ok else None


def utilization(
    now_tpm: float, rec: CapacityRecord | None, replicas: int, model: str
) -> dict:
    usable = (
        rec is not None
        and rec.max_tokens_per_min
        and rec.replicas == replicas
        and rec.model == model
    )
    if not usable:
        return {
            "state": "needs_loadtest",
            "pct": None,
            "now_tpm": now_tpm,
            "max_tpm": None,
            "source": None,
        }
    return {
        "state": "ok",
        "pct": now_tpm / rec.max_tokens_per_min * 100,
        "now_tpm": now_tpm,
        "max_tpm": rec.max_tokens_per_min,
        "source": {
            "loadtest_id": rec.loadtest_id,
            "created_at": rec.created_at.isoformat(),
            "replicas": rec.replicas,
            "model": rec.model,
        },
    }


def period_summary(series: list[tuple[datetime, float]], edge: float = 90.0) -> dict:
    if not series:
        return {"max": None, "avg": None, "over_edge": None}
    values = [v for _, v in sorted(series, key=lambda p: p[0])]
    crossings = sum(
        1
        for prev, cur in zip([float("-inf")] + values, values)
        if cur >= edge and prev < edge
    )
    return {
        "max": max(values),
        "avg": sum(values) / len(values),
        "over_edge": crossings,
    }


def tenant_usage(
    records: Iterable[dict], limits: dict[str, int | None], now: datetime
) -> list[dict]:
    minute_ago = now - timedelta(seconds=60)
    hour_ago = now - timedelta(hours=1)
    tokens: dict[str, int] = {}
    throttled: dict[str, int] = {}
    for tenant in limits:
        if tenant and tenant != LOADTEST_TENANT:
            tokens[tenant] = 0
            throttled[tenant] = 0
    for r in records:
        tenant = r.get("tenant") or ""
        if not tenant or tenant == LOADTEST_TENANT:
            continue
        tokens.setdefault(tenant, 0)
        throttled.setdefault(tenant, 0)
        ts = r["ts"]
        if ts > minute_ago and ts <= now:
            tokens[tenant] += r.get("input_tokens", 0) + r.get("output_tokens", 0)
        if r.get("status") == 429 and ts > hour_ago and ts <= now:
            throttled[tenant] += 1
    out = []
    for tenant in sorted(tokens):
        limit = limits.get(tenant)
        out.append(
            {
                "tenant": tenant,
                "tokens_last_min": tokens[tenant],
                "limit_tpm": limit,
                "pct": None
                if limit is None
                else (tokens[tenant] / limit * 100 if limit else 100.0),
                "throttled_1h": throttled[tenant],
            }
        )
    return out


def idle_replicas(
    running_by_pod: dict[str, list[tuple[datetime, float]]],
    now: datetime,
    minutes: int = 5,
    step_s: int = 30,
) -> list[str]:
    """Pods with samples in the window, all zero, whose series covers its start."""
    start = now - timedelta(minutes=minutes)
    old_enough = start + timedelta(seconds=step_s)
    idle = []
    for pod, series in running_by_pod.items():
        in_window = [v for t, v in series if start <= t <= now]
        if not in_window or min(t for t, _ in series) > old_enough:
            continue
        if all(v == 0 for v in in_window):
            idle.append(pod)
    return sorted(idle)


def cache_hit_rate(prompt_tokens: float, cached_tokens: float) -> float | None:
    return cached_tokens / prompt_tokens if prompt_tokens > 0 else None
