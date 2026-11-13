from datetime import UTC, datetime, timedelta

import pytest

from ttalkkak.capacity import (
    CapacityRecord,
    Stage,
    cache_hit_rate,
    capacity_from_stages,
    idle_replicas,
    period_summary,
    tenant_usage,
    utilization,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def stage(users, ttft, tpm, err=0.0):
    return Stage(
        users=users,
        ttft_p99_ms=ttft,
        itl_p99_ms=20,
        output_tok_s=100,
        tokens_per_min=tpm,
        error_rate=err,
    )


def rec(max_tpm=21500.0, replicas=1, model="m"):
    return CapacityRecord(
        loadtest_id="20261113-100000",
        created_at=NOW,
        replicas=replicas,
        model=model,
        ttft_target_ms=1000,
        max_tokens_per_min=max_tpm,
    )


def test_capacity_picks_highest_stage_within_target():
    stages = [
        stage(1, 200, 1000),
        stage(5, 400, 5000),
        stage(10, 900, 9000),
        stage(20, 1500, 12000),
    ]
    assert capacity_from_stages(stages, 1000) == 9000


def test_capacity_none_when_all_over():
    assert capacity_from_stages([stage(1, 1200, 1000)], 1000) is None


def test_capacity_ignores_stage_with_errors():
    stages = [stage(1, 200, 1000), stage(5, 400, 5000, err=0.1)]
    assert capacity_from_stages(stages, 1000) == 1000


def test_utilization_pct():
    u = utilization(9200, rec(), 1, "m")
    assert u["state"] == "ok"
    assert u["pct"] == pytest.approx(42.79, abs=0.01)
    assert u["max_tpm"] == 21500
    assert u["source"]["loadtest_id"] == "20261113-100000"


@pytest.mark.parametrize(
    "r,replicas,model",
    [
        (None, 1, "m"),
        (rec(replicas=1), 2, "m"),
        (rec(), 1, "other"),
        (rec(max_tpm=None), 1, "m"),
    ],
)
def test_utilization_needs_loadtest(r, replicas, model):
    u = utilization(100, r, replicas, model)
    assert u["state"] == "needs_loadtest"
    assert u["pct"] is None


def test_utilization_needs_loadtest_on_replica_mismatch():
    assert utilization(1, rec(replicas=1), 3, "m")["state"] == "needs_loadtest"


def test_period_summary_counts_crossings():
    s = [(NOW + timedelta(minutes=i), v) for i, v in enumerate([50, 95, 96, 40, 91])]
    out = period_summary(s)
    assert out["over_edge"] == 2
    assert out["max"] == 96
    assert out["avg"] == pytest.approx(74.4)


def test_period_summary_empty():
    assert period_summary([]) == {"max": None, "avg": None, "over_edge": None}


def log(tenant, secs_ago, status=200, i=100, o=50):
    return {
        "ts": NOW - timedelta(seconds=secs_ago),
        "tenant": tenant,
        "status": status,
        "input_tokens": i,
        "output_tokens": o,
    }


def test_tenant_usage_excludes_loadtest_and_empty():
    out = tenant_usage(
        [log("a", 10), log("", 10), log("ttalkkak-loadtest", 10)], {"a": 300}, NOW
    )
    assert [r["tenant"] for r in out] == ["a"]
    assert out[0]["tokens_last_min"] == 150
    assert out[0]["pct"] == 50.0


def test_tenant_usage_counts_429_last_hour():
    out = tenant_usage(
        [
            log("a", 10, 429),
            log("a", 3000, 429),
            log("a", 4000, 200),
            log("a", 3700, 429),
        ],
        {},
        NOW,
    )
    assert out[0]["throttled_1h"] == 2
    assert out[0]["limit_tpm"] is None and out[0]["pct"] is None


def test_idle_replicas_five_minutes():
    def series(vals):
        return [(NOW - timedelta(minutes=5 - i), v) for i, v in enumerate(vals)]

    out = idle_replicas(
        {
            "busy": series([0, 0, 0, 1, 0, 0]),
            "idle": series([0] * 6),
            "new": [(NOW - timedelta(minutes=1), 0.0), (NOW, 0.0)],
        },
        NOW,
    )
    assert out == ["idle"]


def test_cache_hit_rate_zero_prompt():
    assert cache_hit_rate(0, 0) is None
    assert cache_hit_rate(200, 50) == 0.25
