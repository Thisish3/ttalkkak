import json
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel, ValidationError

from ttalkkak.errors import TtalkkakError

LOADTEST_TENANT = "ttalkkak-loadtest"


class Stage(BaseModel):
    users: int
    ttft_p99_ms: float
    itl_p99_ms: float
    output_tok_s: float
    tokens_per_min: float
    error_rate: float


class Probe(Stage):
    """One concurrency level of guidellm's goodput search."""

    request_rate: float
    attainment: float
    attainment_lower: float
    attainment_upper: float
    determined_requests: int
    passed: bool
    resolved: bool
    aborted: bool = False
    duration_s: float | None = None


class CapacityRecord(BaseModel):
    loadtest_id: str
    created_at: datetime
    replicas: int
    model: str
    ttft_target_ms: float
    max_tokens_per_min: float | None
    method: str = "stages"
    target_attainment: float | None = None
    itl_target_ms: float | None = None
    stop_reason: str | None = None
    capacity_streams: int | None = None
    confirmed: bool | None = None
    confirm_rate_rps: float | None = None
    knee_streams: float | None = None
    knee_output_tok_s: float | None = None
    unconfirmed_attempt: dict | None = None

    @property
    def usable(self) -> bool:
        """Has a capacity that did not fail the open-loop check."""
        return bool(self.max_tokens_per_min) and self.confirmed is not False


def keep_or_replace(old: CapacityRecord | None, new: CapacityRecord) -> CapacityRecord:
    """Which record a new load-test result leaves for its replica count.

    An unconfirmed result never replaces a usable record of the same model; it
    is noted on that record instead. A result without capacity never erases a
    usable record of another model.
    """
    if old is None or not old.usable:
        return new
    if new.confirmed is False and new.model == old.model:
        attempt = {
            "loadtest_id": new.loadtest_id,
            "created_at": new.created_at.isoformat(),
        }
        return old.model_copy(update={"unconfirmed_attempt": attempt})
    if not new.max_tokens_per_min and new.model != old.model:
        return old
    return new


def parse_capacity(
    raw: str | None, namespace: str = "<네임스페이스>"
) -> dict[int, CapacityRecord]:
    """Records by replica count from the ConfigMap value.

    New format: {"<replicas>": record, ...}. The old single record is read as
    the entry for its own replica count.
    """
    try:
        data = json.loads(raw) if raw else {}
        if not data:
            return {}
        if "replicas" in data:
            rec = CapacityRecord.model_validate(data)
            return {rec.replicas: rec}
        return {int(n): CapacityRecord.model_validate(r) for n, r in data.items()}
    except (ValueError, ValidationError, TypeError, AttributeError) as err:
        raise TtalkkakError(
            f"용량 기록(ConfigMap ttalkkak-capacity)을 읽을 수 없습니다 "
            f"({type(err).__name__}). `kubectl delete configmap ttalkkak-capacity "
            f"-n {namespace}`로 지운 뒤 부하 시험을 다시 실행하세요"
        ) from err


def merge_capacity(
    raw: str | None, rec: CapacityRecord, namespace: str = "<네임스페이스>"
) -> str:
    """ConfigMap value with rec replacing only the entry for its replica count."""
    records = parse_capacity(raw, namespace)
    records[rec.replicas] = keep_or_replace(records.get(rec.replicas), rec)
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
    """Counts with no record, or whose record failed the open-loop check."""
    return [
        n
        for n in range(1, max_replicas + 1)
        if n not in records or not records[n].usable
    ]


def missing_loadtest_text(
    records: dict[int, CapacityRecord], max_replicas: int
) -> str | None:
    missing = missing_loadtest_counts(records, max_replicas)
    if not missing:
        return None
    return "부하 시험 필요: " + ", ".join(
        f"{n}개(확인 실패)"
        if n in records and records[n].confirmed is False
        else f"{n}개"
        for n in missing
    )


def capacity_from_stages(stages: list[Stage], target_ms: float) -> float | None:
    ok = [s for s in stages if s.ttft_p99_ms <= target_ms and s.error_rate == 0]
    return max(ok, key=lambda s: s.users).tokens_per_min if ok else None


_NO_CAPACITY = {
    "indeterminate_at_minimum": (
        "요청 수가 적어 가장 낮은 단계에서도 목표 달성 여부를 판정할 수 없습니다. "
        "단계 시간을 늘려 다시 시험하세요"
    ),
    "objectives_unmet_at_minimum": "동시 사용자 1명에서도 목표를 지키지 못했습니다",
}


def passing_levels(probes: list[Probe]) -> list[Probe]:
    """Probes that passed with a resolved interval, no abort, no errors; high first."""
    ok = [
        p
        for p in probes
        if p.passed and p.resolved and not p.aborted and p.error_rate == 0
    ]
    return sorted(ok, key=lambda p: p.users, reverse=True)


def capacity_from_search(
    probes: list[Probe], stop_reason: str | None
) -> tuple[Probe | None, str | None]:
    """Highest probe that passed with a resolved interval, no abort and no errors.

    Returns (probe, None) or (None, Korean reason). Searches that stopped at the
    minimum never give a capacity.
    """
    if stop_reason in _NO_CAPACITY:
        return None, _NO_CAPACITY[stop_reason]
    ok = passing_levels(probes)
    if ok:
        return ok[0], None
    passed = [p for p in probes if p.passed and not p.aborted]
    if any(p.error_rate == 0 and not p.resolved for p in passed):
        return None, (
            "목표를 지킨 단계가 있지만 요청 수가 적어 확정하지 못했습니다. "
            "단계 시간을 늘려 다시 시험하세요"
        )
    if passed:
        return None, "목표를 지킨 단계마다 오류 요청이 있었습니다"
    return None, "목표를 지킨 단계가 없습니다"


def utilization(
    now_tpm: float, rec: CapacityRecord | None, replicas: int, model: str
) -> dict:
    matches = rec is not None and rec.replicas == replicas and rec.model == model
    if not matches or not rec.usable:
        return {
            "state": "unconfirmed"
            if matches and rec.confirmed is False and rec.max_tokens_per_min
            else "needs_loadtest",
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
            "ttft_target_ms": rec.ttft_target_ms,
            "target_attainment": rec.target_attainment,
            "capacity_streams": rec.capacity_streams,
            "confirmed": rec.confirmed,
            "knee_streams": rec.knee_streams,
            "unconfirmed_attempt": rec.unconfirmed_attempt,
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
