import json
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
    assert u["source"]["ttft_target_ms"] == rec().ttft_target_ms


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


def test_tenant_usage_includes_limit_only_tenants():
    out = tenant_usage([], {"b": 100, "": 5, "ttalkkak-loadtest": 5}, NOW)
    assert out == [
        {
            "tenant": "b",
            "tokens_last_min": 0,
            "limit_tpm": 100,
            "pct": 0.0,
            "throttled_1h": 0,
        }
    ]


def test_tenant_usage_zero_limit_is_blocked():
    out = tenant_usage([], {"b": 0}, NOW)
    assert out[0]["pct"] == 100.0


def test_idle_replicas_tolerates_one_step_after_start():
    s = [
        (NOW - timedelta(minutes=5) + timedelta(seconds=20 + 30 * i), 0.0)
        for i in range(10)
    ]
    assert idle_replicas({"p": s}, NOW) == ["p"]


def test_idle_replicas_vanished_pod_not_idle():
    s = [(NOW - timedelta(minutes=10), 0.0), (NOW - timedelta(minutes=8), 0.0)]
    assert idle_replicas({"gone": s}, NOW) == []


def test_utilization_not_capped():
    assert utilization(43000, rec(), 1, "m")["pct"] == pytest.approx(200.0)


def test_period_summary_first_value_over_edge_counts_once():
    s = [(NOW + timedelta(minutes=i), v) for i, v in enumerate([95, 99, 50])]
    assert period_summary(s)["over_edge"] == 1


def _rec(n, tpm=1000.0):
    from ttalkkak.capacity import CapacityRecord

    return CapacityRecord(
        loadtest_id=f"l{n}", created_at=datetime(2026, 11, 16, tzinfo=UTC),
        replicas=n, model="m", ttft_target_ms=500, max_tokens_per_min=tpm,
    )  # fmt: skip


def test_capacity_records_per_count_and_old_format():
    from ttalkkak.capacity import (
        capacity_counts,
        merge_capacity,
        missing_loadtest_text,
        parse_capacity,
        record_for,
    )

    assert parse_capacity(None) == {} and parse_capacity("{}") == {}
    old = _rec(2).model_dump_json()
    recs = parse_capacity(old)
    assert capacity_counts(recs) == [2] and record_for(recs, 2).loadtest_id == "l2"
    assert record_for(recs, 1) is None
    merged = parse_capacity(merge_capacity(old, _rec(1)))
    assert capacity_counts(merged) == [1, 2]
    again = parse_capacity(
        merge_capacity(
            json.dumps({"1": json.loads(_rec(1).model_dump_json())}), _rec(1, 2000.0)
        )
    )
    assert record_for(again, 1).max_tokens_per_min == 2000.0
    assert missing_loadtest_text(merged, 3) == "부하 시험 필요: 3개"
    assert (
        missing_loadtest_text(parse_capacity(None), 3)
        == "부하 시험 필요: 1개, 2개, 3개"
    )
    assert missing_loadtest_text(merged, 2) is None


def test_corrupt_capacity_raises_korean_error():
    import pytest

    from ttalkkak.capacity import parse_capacity
    from ttalkkak.errors import TtalkkakError

    for raw in ("{not json", '{"1": {"replicas": "x"}}'):
        with pytest.raises(TtalkkakError, match="읽을 수 없습니다") as e:
            parse_capacity(raw, "ns1")
        assert "kubectl delete configmap ttalkkak-capacity -n ns1" in str(e.value)


def probe(users, tpm, *, passed=True, resolved=True, aborted=False, err=0.0):
    from ttalkkak.capacity import Probe

    return Probe(
        users=users, ttft_p99_ms=100, itl_p99_ms=20, output_tok_s=10 * users,
        tokens_per_min=tpm, error_rate=err, request_rate=users / 2,
        attainment=1.0 if passed else 0.5, attainment_lower=0.9,
        attainment_upper=1.0, determined_requests=150, passed=passed,
        resolved=resolved, aborted=aborted,
    )  # fmt: skip


def test_search_capacity_is_highest_resolved_passing_probe():
    from ttalkkak.capacity import capacity_from_search

    probes = [probe(1, 100), probe(2, 200), probe(4, 400, passed=False), probe(3, 300)]
    best, why = capacity_from_search(probes, "converged")
    assert best.users == 3 and best.tokens_per_min == 300 and why is None


def test_search_capacity_skips_unresolved_aborted_and_errored_probes():
    from ttalkkak.capacity import capacity_from_search

    probes = [
        probe(1, 100),
        probe(2, 200, err=0.01),
        probe(4, 400, resolved=False),
        probe(8, 800, aborted=True),
    ]
    best, _ = capacity_from_search(probes, "constraint_stopped_escalation")
    assert best.users == 1


@pytest.mark.parametrize(
    "reason,text",
    [
        ("indeterminate_at_minimum", "판정할 수 없습니다"),
        ("objectives_unmet_at_minimum", "1명에서도"),
    ],
)
def test_search_without_capacity_explains_why(reason, text):
    from ttalkkak.capacity import capacity_from_search

    probes = [probe(1, 100, passed=False, resolved=False)]
    best, why = capacity_from_search(probes, reason)
    assert best is None and text in why


def test_indeterminate_search_never_yields_capacity_even_with_a_pass():
    from ttalkkak.capacity import capacity_from_search

    best, why = capacity_from_search([probe(1, 100)], "indeterminate_at_minimum")
    assert best is None and why


def test_search_with_only_unresolved_passes_has_no_capacity():
    from ttalkkak.capacity import capacity_from_search

    best, why = capacity_from_search([probe(1, 100, resolved=False)], "converged")
    assert best is None and "요청 수" in why


def test_search_with_only_errored_passes_has_no_capacity():
    from ttalkkak.capacity import capacity_from_search

    best, why = capacity_from_search([probe(1, 100, err=0.1)], "max_streams_reached")
    assert best is None and "오류" in why


def test_old_capacity_record_parses_with_new_fields_defaulted():
    from ttalkkak.capacity import parse_capacity

    old = (
        '{"1": {"loadtest_id":"old","created_at":"2026-11-01T00:00:00Z",'
        '"replicas":1,"model":"m","ttft_target_ms":500,"max_tokens_per_min":10.0}}'
    )
    r = parse_capacity(old)[1]
    assert r.method == "stages" and r.confirmed is None and r.knee_streams is None
    assert utilization(5, r, 1, "m")["state"] == "ok"


def test_goodput_record_round_trips_through_the_configmap_value():
    from ttalkkak.capacity import merge_capacity, parse_capacity

    r = CapacityRecord(
        **{
            **rec().model_dump(),
            "method": "goodput", "target_attainment": 0.95, "itl_target_ms": 50.0,
            "stop_reason": "converged", "capacity_streams": 6, "confirmed": True,
            "confirm_rate_rps": 2.7, "knee_streams": 7.5, "knee_output_tok_s": 480.0,
        }
    )  # fmt: skip
    back = parse_capacity(merge_capacity(None, r))[1]
    assert back == r and back.method == "goodput" and back.knee_streams == 7.5
    src = utilization(5, back, 1, "m")["source"]
    assert src["target_attainment"] == 0.95 and src["capacity_streams"] == 6


def test_unconfirmed_record_is_not_used_and_needs_a_new_loadtest():
    from ttalkkak.capacity import missing_loadtest_counts, missing_loadtest_text

    bad = CapacityRecord(**{**rec(replicas=2).model_dump(), "confirmed": False})
    u = utilization(100, bad, 2, "m")
    assert u["state"] == "unconfirmed" and u["pct"] is None
    records = {1: rec(), 2: bad}
    assert missing_loadtest_counts(records, 2) == [2]
    assert missing_loadtest_text(records, 3) == "부하 시험 필요: 2개(확인 실패), 3개"
