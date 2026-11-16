import json
from datetime import UTC, datetime, timedelta

from ttalkkak.autoscale import Rules, State
from ttalkkak.capacity import CapacityRecord
from ttalkkak.live import prom as q
from ttalkkak.ops_api.autoscaler import K8s, parse_decisions, tick

NS = "ttalkkak"
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
REC = CapacityRecord(
    loadtest_id="l1", created_at=T0, replicas=1, model="m",
    ttft_target_ms=500, max_tokens_per_min=1000.0,
)  # fmt: skip


class FakeK8s:
    def __init__(self, enabled="true", desired=1, ready=1, record=REC, raw=None):
        self.cms = {
            "ttalkkak-autoscale": {
                "enabled": enabled, "max_replicas": "3", "deployment": "vllm-x",
            },
            "ttalkkak-capacity": {
                "capacity.json": raw
                or (record.model_dump_json() if record else "{}")
            },
        }  # fmt: skip
        self.scale = (desired, ready)
        self.sets = []

    def get_configmap(self, ns, name):
        return self.cms[name]

    def get_scale(self, ns, deploy):
        return self.scale

    def set_scale(self, ns, deploy, n):
        self.sets.append((deploy, n))


class FakeProm:
    def __init__(self, tpm=900.0, waiting=0.0, error=None):
        self.tpm, self.waiting, self.error = tpm, waiting, error

    def instant(self, query):
        if self.error:
            raise self.error
        if query == q.TPM:
            return [({}, self.tpm)]
        if query == q.WAITING:
            return [({"pod": "a"}, self.waiting)]
        if query == q.RUNNING:
            return [({"pod": "a", "model_name": "m"}, 1.0)]
        raise AssertionError(query)

    def range(self, query, minutes, step_s):
        return [
            ({"pod": "a"}, [(T0 - timedelta(seconds=30 * i), 1.0) for i in range(10)])
        ]


def run(k8s, prom, ticks, step_s=10, rules=None):
    state, out = State(), []
    for i in range(ticks):
        d, state = tick(
            k8s, prom, state, rules or Rules(), T0 + timedelta(seconds=step_s * i), NS
        )
        out.append(d)
    return out, state


def test_tick_disabled_returns_none(capsys):
    d, _ = tick(FakeK8s(enabled="false"), FakeProm(), State(), Rules(), T0, NS)
    assert d is None
    assert capsys.readouterr().out == ""


def test_tick_scales_up_via_k8s(capsys):
    k8s = FakeK8s()
    out, _ = run(k8s, FakeProm(tpm=900.0), 14)
    ups = [d for d in out if d and d.action == "up"]
    assert len(ups) == 1 and ups[0].to_replicas == 2
    assert k8s.sets == [("vllm-x", 2)]
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "up" in ln]
    assert lines[0].startswith("AUTOSCALE ")
    assert json.loads(lines[0][len("AUTOSCALE ") :])["action"] == "up"


def test_tick_holds_on_prom_error(capsys):
    from ttalkkak.live.prom import PromError

    k8s = FakeK8s()
    d, _ = tick(k8s, FakeProm(error=PromError("끊김")), State(), Rules(), T0, NS)
    assert d.action == "hold" and "오류" in d.reason and "끊김" in d.reason
    assert k8s.sets == []
    out = capsys.readouterr().out
    assert out.startswith("AUTOSCALE ") and "오류" in out


def test_tick_logs_hold_only_on_reason_change(capsys):
    k8s = FakeK8s()
    run(k8s, FakeProm(tpm=500.0), 5)
    assert len(capsys.readouterr().out.splitlines()) == 1
    state = State()
    for i, tpm in enumerate((500.0, 500.0, None)):
        prom = FakeProm(tpm=tpm if tpm is not None else 0.0)
        k8s.scale = (1, 1) if tpm is not None else (2, 1)
        _, state = tick(k8s, prom, state, Rules(), T0 + timedelta(seconds=10 * i), NS)
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_tick_no_capacity_record_holds(capsys):
    d, _ = tick(FakeK8s(record=None), FakeProm(), State(), Rules(), T0, NS)
    assert d.action == "hold" and "용량 기록" in d.reason


def test_parse_decisions_skips_other_lines():
    good = (
        '{"t":"2026-11-16T12:00:00Z","action":"up","from_replicas":1,'
        '"to_replicas":2,"reason":"r"}'
    )
    rows = parse_decisions(["noise", "AUTOSCALE " + good, "AUTOSCALE {bad", ""])
    assert [r["action"] for r in rows] == ["up"]


def test_k8s_transport_scale_calls():
    calls = []

    def transport(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/scale"):
            return {"spec": {"replicas": 2}, "status": {"readyReplicas": 1}}
        return {"data": {"enabled": "true"}}

    k = K8s(transport)
    assert k.get_scale(NS, "vllm-x") == (2, 1)
    k.set_scale(NS, "vllm-x", 3)
    assert k.get_configmap(NS, "ttalkkak-autoscale") == {"enabled": "true"}
    assert calls[0] == (
        "GET",
        f"/apis/apps/v1/namespaces/{NS}/deployments/vllm-x/scale",
        None,
    )
    assert calls[1][0] == "PATCH" and calls[1][2] == {"spec": {"replicas": 3}}


def _per_count(*recs):
    return json.dumps({str(r.replicas): json.loads(r.model_dump_json()) for r in recs})


def test_tick_uses_record_for_the_current_count():
    rec2 = REC.model_copy(update={"replicas": 2, "max_tokens_per_min": 2000.0})
    k8s = FakeK8s(desired=2, ready=2, raw=_per_count(REC, rec2))
    d, _ = tick(k8s, FakeProm(tpm=500.0), State(), Rules(), T0, NS)
    assert d.action == "hold" and d.reason == "조건 없음"


def test_tick_holds_when_current_count_has_no_record():
    k8s = FakeK8s(desired=2, ready=2, raw=_per_count(REC))
    d, _ = tick(k8s, FakeProm(tpm=500.0), State(), Rules(), T0, NS)
    assert d.action == "hold" and "용량 기록 없음" in d.reason


def _lines(capsys):
    return [
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("AUTOSCALE ")
    ]


def test_cooldown_hold_is_deduped_by_stable_key(capsys):
    k8s = FakeK8s()
    _, st = run(k8s, FakeProm(tpm=900.0), 14)
    capsys.readouterr()
    k8s.scale = (2, 2)
    rec2 = REC.model_copy(update={"replicas": 2, "max_tokens_per_min": 2000.0})
    k8s.cms["ttalkkak-capacity"]["capacity.json"] = _per_count(REC, rec2)
    base = st.last_change
    for i in range(1, 25):
        _, st = tick(
            k8s, FakeProm(tpm=1000.0), st, Rules(), base + timedelta(seconds=10 * i), NS
        )
    cool = [ln for ln in _lines(capsys) if "쿨다운" in ln]
    assert len(cool) == 1


def test_ready_after_s_emitted_by_loop(capsys):
    k8s = FakeK8s()
    out, st = run(k8s, FakeProm(tpm=900.0), 14)
    up = next(d for d in out if d and d.action == "up")
    capsys.readouterr()
    k8s.scale = (2, 1)
    _, st = tick(
        k8s, FakeProm(tpm=900.0), st, Rules(), up.t + timedelta(seconds=10), NS
    )
    assert not [ln for ln in _lines(capsys) if '"ready"' in ln]
    k8s.scale = (2, 2)
    _, st = tick(
        k8s, FakeProm(tpm=900.0), st, Rules(), up.t + timedelta(seconds=75), NS
    )
    _, st = tick(
        k8s, FakeProm(tpm=900.0), st, Rules(), up.t + timedelta(seconds=85), NS
    )
    ready = [
        json.loads(ln[len("AUTOSCALE ") :]) for ln in _lines(capsys) if '"ready"' in ln
    ]
    assert len(ready) == 1
    assert ready[0]["action"] == "ready" and ready[0]["ready_after_s"] == 75
    assert ready[0]["to_replicas"] == 2
    rows = parse_decisions(["AUTOSCALE " + json.dumps(ready[0])])
    assert rows[0]["ready_after_s"] == 75


def test_token_is_reread_per_request(tmp_path):
    import httpx

    from ttalkkak.ops_api.autoscaler import in_cluster_transport

    (tmp_path / "token").write_text("one\n")
    (tmp_path / "ca.crt").write_text("")
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={})

    call = in_cluster_transport(
        sa_dir=tmp_path, http_transport=httpx.MockTransport(handler)
    )
    call("GET", "/x", None)
    (tmp_path / "token").write_text("two\n")
    call("GET", "/x", None)
    assert seen == ["Bearer one", "Bearer two"]


def test_missing_token_does_not_crash_and_holds(tmp_path, capsys):
    from ttalkkak.ops_api.autoscaler import in_cluster_transport

    k8s = K8s(in_cluster_transport(sa_dir=tmp_path / "none"))
    d, _ = tick(k8s, FakeProm(), State(), Rules(), T0, NS)
    assert d.action == "hold" and d.reason.startswith("오류")
    assert "0→0" not in d.reason


def test_early_error_uses_last_known_replicas(capsys):
    k8s = FakeK8s(desired=2, ready=2)
    _, st = tick(k8s, FakeProm(tpm=500.0), State(), Rules(), T0, NS)
    d, _ = tick(
        k8s,
        FakeProm(error=RuntimeError("끊김")),
        st,
        Rules(),
        T0 + timedelta(seconds=10),
        NS,
    )
    assert (d.from_replicas, d.to_replicas) == (2, 2)
    first, _ = tick(
        FakeK8s(), FakeProm(error=RuntimeError("x")), State(), Rules(), T0, NS
    )
    assert first.from_replicas == 1  # scale is read before Prometheus


def test_error_before_any_scale_read_leaves_counts_unset():
    class Broken(FakeK8s):
        def get_configmap(self, ns, name):
            raise RuntimeError("api 끊김")

    d, _ = tick(Broken(), FakeProm(), State(), Rules(), T0, NS)
    assert d.from_replicas is None and d.to_replicas is None
    assert "api 끊김" in d.reason


def test_corrupt_capacity_record_holds_in_korean():
    k8s = FakeK8s(raw="{not json")
    d, _ = tick(k8s, FakeProm(), State(), Rules(), T0, NS)
    assert d.action == "hold"
    assert d.reason.startswith(
        "오류: 용량 기록(ConfigMap ttalkkak-capacity)을 읽을 수 없습니다"
    )
    assert f"kubectl delete configmap ttalkkak-capacity -n {NS}" in d.reason


def test_missing_record_reason_names_the_count():
    k8s = FakeK8s(desired=2, ready=2, raw=_per_count(REC))
    d, _ = tick(k8s, FakeProm(tpm=500.0), State(), Rules(), T0, NS)
    assert d.reason == "용량 기록 없음: 복제본 2개 부하 시험 필요"


def test_no_metrics_reason_is_distinct():
    class NoTpm(FakeProm):
        def instant(self, query):
            return [] if query == q.TPM else super().instant(query)

    d, _ = tick(FakeK8s(), NoTpm(), State(), Rules(), T0, NS)
    assert d.action == "hold" and "지표 없음" in d.reason
    assert "용량 기록 없음" not in d.reason
