import json
import sqlite3
from datetime import UTC, datetime, timedelta
from subprocess import CompletedProcess

import httpx
import pytest
from starlette.testclient import TestClient

from ttalkkak.capacity import CapacityRecord
from ttalkkak.chips import chip_by_name
from ttalkkak.cost.model import CostConfig
from ttalkkak.live import prom as P
from ttalkkak.live.prom import Prom, PromError
from ttalkkak.live.snapshot import build_snapshot
from ttalkkak.ops_cli import _ui_chip
from ttalkkak.tenants.store import TenantStore
from ttalkkak.ui.app import UiDeps, allowed_hosts, create_app

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
CHIP = chip_by_name("nvidia-gpu")
POWER = CHIP.telemetry.metrics["power"]


def vec(*rows):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": m, "value": [NOW.timestamp(), str(v)]} for m, v in rows
            ],
        },
    }


def matrix(*rows):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": m,
                    "values": [
                        [
                            (
                                NOW - timedelta(seconds=30 * (len(vs) - 1 - i))
                            ).timestamp(),
                            str(v),
                        ]
                        for i, v in enumerate(vs)
                    ],
                }
                for m, vs in rows
            ],
        },
    }


def prom_with(answers: dict[str, dict]) -> Prom:
    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        if q in answers:
            body = answers[q]
            if (
                req.url.path.endswith("query_range")
                and body["data"]["resultType"] == "vector"
            ):
                body = {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {"metric": r["metric"], "values": [r["value"]]}
                            for r in body["data"]["result"]
                        ],
                    },
                }
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=vec())

    return Prom(
        "http://prom:9090", httpx.Client(transport=httpx.MockTransport(handler))
    )


def down_prom() -> Prom:
    def handler(req):
        raise httpx.ConnectError("refused")

    return Prom(
        "http://prom:9090", httpx.Client(transport=httpx.MockTransport(handler))
    )


def good_answers(kv=(0.2, 0.3)):
    pods = ["vllm-a", "vllm-b"]
    return {
        P.TPM: vec(({}, 600)),
        P.RUNNING: vec(*[({"pod": p, "model_name": "m"}, 2) for p in pods]),
        P.WAITING: vec(*[({"pod": p}, 1) for p in pods]),
        P.KV: vec(*[({"pod": p}, k) for p, k in zip(pods, kv)]),
        P.PROMPT: vec(*[({"pod": p}, 100) for p in pods]),
        P.CACHED: vec(*[({"pod": p}, 25) for p in pods]),
        P.TTFT_P99: vec(({}, 0.4)),
        P.ITL_P99: vec(({}, 0.02)),
        P.OUTPUT_TPS: vec(({}, 50)),
        f"sum({POWER})": vec(({}, 300)),
    }


PODS = [
    {"name": "vllm-a", "ready": "1/1", "restarts": 0, "phase": "Running"},
    {"name": "vllm-b", "ready": "1/1", "restarts": 0, "phase": "Running"},
]
REC = CapacityRecord(
    loadtest_id="l1", created_at=NOW, replicas=2, model="m",
    ttft_target_ms=500, max_tokens_per_min=1200,
)  # fmt: skip
AUTO = {"enabled": True, "max_replicas": 4, "deployment": "vllm", "decisions": []}


COST = CostConfig()


def snap(prom, logs=(), limits=None, rec=REC, cost=COST):
    return build_snapshot(
        prom, PODS, list(logs), limits or {}, rec, CHIP, "m", NOW, AUTO, cost=cost
    )


def log(tenant, tokens, status=200, ago=10):
    return json.dumps({
        "ts": (NOW - timedelta(seconds=ago)).isoformat(), "tenant": tenant,
        "model": "m", "status": status, "input_tokens": tokens, "output_tokens": 0,
    })  # fmt: skip


def test_prom_instant_parses_vector():
    p = prom_with({"x": vec(({"pod": "a"}, 1.5), ({"pod": "b"}, "NaN"))})
    assert p.instant("x") == [({"pod": "a"}, 1.5)]


def test_prom_range_parses_matrix():
    p = prom_with({"x": matrix(({"pod": "a"}, [0, 1]))})
    ((labels, series),) = p.range("x", 5, 30)
    assert labels == {"pod": "a"} and [v for _, v in series] == [0.0, 1.0]
    assert series[0][0].tzinfo is not None


def test_prom_error_on_connection():
    with pytest.raises(PromError):
        down_prom().instant("x")


def test_prom_error_on_http_status():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(PromError):
        Prom("http://prom", http).instant("x")


def test_snapshot_full():
    s = snap(prom_with(good_answers()), [log("acme", 100)], {"acme": 1000})
    assert s["headline"]["concurrent"] == 4
    assert s["headline"]["ttft_p99_ms"] == pytest.approx(400)
    assert s["headline"]["itl_p99_ms"] == pytest.approx(20)
    assert s["headline"]["power_w"] == 300
    assert s["headline"]["krw_per_hour"] == pytest.approx(
        300 * 150 * 1.4 / 3.6e6 * 3600
    )
    assert s["capacity"]["state"] == "ok" and s["capacity"]["pct"] == pytest.approx(50)
    assert s["capacity"]["signals"]["waiting"] == 2
    assert s["capacity"]["signals"]["ready_replicas"] == 2
    assert s["capacity"]["signals"]["max_replicas"] == 4
    assert s["tenants"][0]["tenant"] == "acme"
    assert s["replicas"][0]["hit_rate"] == pytest.approx(0.25)
    assert s["autoscale"]["enabled"] is True
    assert s["errors"] == {}


def test_snapshot_partial_when_prom_down():
    s = snap(down_prom(), [log("acme", 100)], {"acme": 1000})
    assert "headline" in s["errors"] and "capacity" in s["errors"]
    assert all(isinstance(v, str) and v for v in s["errors"].values())
    assert s["tenants"][0]["tokens_last_min"] == 100


def test_snapshot_empty_query_is_error_not_crash():
    s = snap(prom_with({}))
    assert "capacity" in s["errors"] and "headline" in s["errors"]
    assert s["headline"]["concurrent"] is None


def test_snapshot_needs_loadtest_without_record():
    s = snap(prom_with(good_answers()), rec=None)
    assert s["capacity"]["state"] == "needs_loadtest" and s["capacity"]["pct"] is None


def test_power_missing_chip_metric_is_null_with_note():
    chip = CHIP.model_copy(
        update={"telemetry": CHIP.telemetry.model_copy(update={"metrics": {}})}
    )
    s = build_snapshot(
        prom_with(good_answers()), PODS, [], {}, REC, chip, "m", NOW, AUTO,
        cost=CostConfig(),
    )  # fmt: skip
    assert s["headline"]["power_w"] is None and s["headline"]["krw_per_hour"] is None
    assert "power_w" in s["errors"]


def test_krw_per_hour_null_without_cost():
    s = snap(prom_with(good_answers()), cost=None)
    assert s["headline"]["power_w"] == 300 and s["headline"]["krw_per_hour"] is None


def test_recommend_router_weights():
    s = snap(prom_with(good_answers(kv=(0.1, 0.5))))
    assert {"kind": "router_weights"}.items() <= s["recommendations"][0].items()
    assert snap(prom_with(good_answers(kv=(0.2, 0.49))))["recommendations"] == []


def test_recommend_tenant_limit():
    s = snap(prom_with(good_answers()), [log("acme", 960)], {"acme": 1000})
    rec = [r for r in s["recommendations"] if r["kind"] == "tenant_limit"]
    assert rec and rec[0]["tenant"] == "acme"
    s = snap(prom_with(good_answers()), [log("acme", 940)], {"acme": 1000})
    assert not [r for r in s["recommendations"] if r["kind"] == "tenant_limit"]


def kubectl_for(auto=True, cap="{}", calls=None):
    def run(args, input=None):
        if calls is not None:
            calls.append(args)
        if args[:3] == ["get", "pods", "-n"]:
            return CompletedProcess(args, 1, "", "no")
        name = args[2]
        if name == "ttalkkak-autoscale":
            if not auto:
                return CompletedProcess(args, 1, "", "NotFound")
            data = {"enabled": "true", "max_replicas": "4", "deployment": "vllm"}
        else:
            data = {"capacity.json": cap}
        return CompletedProcess(args, 0, json.dumps({"data": data}), "")

    return run


def live_client(tmp_path, prom, kubectl, calls=None, **over):
    kw = dict(  # noqa: C408
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None,
        store=TenantStore(tmp_path / "t.db"),
        cluster_status=lambda: PODS,
        gateway_logs=lambda: [log("acme", 100)],
        prometheus=prom, kubectl=kubectl, chip=CHIP, cost_config=CostConfig(),
    )  # fmt: skip
    deps = UiDeps(**{**kw, **over})
    return TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))


def test_live_route_json(tmp_path):
    calls = []
    c = live_client(
        tmp_path, prom_with(good_answers()), kubectl_for(calls=calls), calls
    )
    body = c.get("/api/live").json()
    assert body["capacity"]["state"] == "needs_loadtest"
    assert body["headline"]["concurrent"] == 4
    assert body["capacity"]["signals"]["max_replicas"] == 4
    assert body["errors"] == {}
    c.get("/api/live")
    assert sum(1 for a in calls if "ttalkkak-autoscale" in a) == 1  # cached 10 s


def test_live_route_missing_autoscale_configmap_falls_back(tmp_path):
    c = live_client(tmp_path, prom_with(good_answers()), kubectl_for(auto=False))
    r = c.get("/api/live")
    assert r.status_code == 200
    body = r.json()
    assert body["capacity"]["signals"]["max_replicas"] == 2
    assert "autoscale" in body["errors"]


def test_live_route_prom_down_is_200(tmp_path):
    c = live_client(tmp_path, down_prom(), kubectl_for())
    r = c.get("/api/live")
    assert r.status_code == 200
    assert {"headline", "capacity"} <= set(r.json()["errors"])
    assert r.json()["tenants"] is not None


def test_live_route_without_prometheus_is_200(tmp_path):
    c = live_client(tmp_path, None, kubectl_for())
    r = c.get("/api/live")
    assert r.status_code == 200 and "headline" in r.json()["errors"]


def test_allow_host_adds_name(tmp_path):
    deps = UiDeps(list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None)
    c = TestClient(
        create_app(tmp_path, deps, allowed_hosts(["desk.tail1234.ts.net"])),
        base_url="http://desk.tail1234.ts.net",
    )
    assert c.get("/api/gpu").status_code == 200
    other = TestClient(
        create_app(tmp_path, deps, allowed_hosts(["desk.tail1234.ts.net"])),
        base_url="http://evil.example",
    )
    assert other.get("/api/gpu").status_code == 403
    assert (
        TestClient(
            create_app(tmp_path, deps, allowed_hosts([])), base_url="http://127.0.0.1"
        )
        .get("/api/gpu")
        .status_code
        == 200
    )


def garbage_prom(body):
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    )
    return Prom("http://prom", http)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "success"},
        {"status": "success", "data": {"result": [{"metric": {}, "value": ["x"]}]}},
        {"status": "success", "data": {"result": [{"value": [1, "2"]}]}},
        {"status": "success", "data": {"result": "nope"}},
        [],
    ],
)
def test_garbage_prom_body_is_prom_error_and_route_200(tmp_path, body):
    with pytest.raises(PromError):
        garbage_prom(body).instant("x")
    r = live_client(tmp_path, garbage_prom(body), kubectl_for()).get("/api/live")
    assert r.status_code == 200 and "headline" in r.json()["errors"]


def test_store_error_gives_200_with_tenants_error(tmp_path):
    class Boom:
        def list_tenants(self):
            raise sqlite3.Error("locked")

    c = live_client(tmp_path, prom_with(good_answers()), kubectl_for(), store=Boom())
    r = c.get("/api/live")
    assert r.status_code == 200 and "tenants" in r.json()["errors"]
    assert r.json()["headline"]["concurrent"] == 4


def test_cluster_status_oserror_gives_200_with_replicas_error(tmp_path):
    def boom():
        raise OSError("kubectl")

    c = live_client(
        tmp_path, prom_with(good_answers()), kubectl_for(), cluster_status=boom
    )
    r = c.get("/api/live")
    assert r.status_code == 200 and "replicas" in r.json()["errors"]


def test_fallback_counts_only_vllm_pods(tmp_path):
    pods = [
        *PODS,
        {"name": "envoy-gw", "ready": "1/1", "restarts": 0, "phase": "Running"},
    ]
    c = live_client(
        tmp_path, prom_with(good_answers()), kubectl_for(auto=False),
        cluster_status=lambda: pods,
    )  # fmt: skip
    body = c.get("/api/live").json()
    assert body["capacity"]["signals"]["max_replicas"] == 2
    assert [r["pod"] for r in body["replicas"]] == ["vllm-a", "vllm-b"]
    assert body["autoscale"]["enabled"] is None


def test_fallback_without_prom_counts_no_pods(tmp_path):
    c = live_client(tmp_path, down_prom(), kubectl_for(auto=False))
    body = c.get("/api/live").json()
    assert body["capacity"]["signals"]["max_replicas"] == 0
    assert "Prometheus" in body["errors"]["autoscale"]


def test_one_request_per_snapshot_when_prom_fails():
    hits = []

    def handler(req):
        hits.append(req)
        raise httpx.ConnectTimeout("hung")

    p = Prom("http://prom", httpx.Client(transport=httpx.MockTransport(handler)))
    s = snap(p)
    assert len(hits) == 1
    assert {"headline", "capacity", "replicas"} <= set(s["errors"])


def test_ui_chip_falls_back_without_gpu():
    assert _ui_chip(None, lambda: None).name == "nvidia-gpu"

    def boom():
        raise OSError("no nvidia-smi")

    assert _ui_chip(None, boom).name == "nvidia-gpu"


def test_snapshot_capacity_carries_the_hour_series_for_the_chart():
    s = snap(prom_with(good_answers()), [log("acme", 100)], {"acme": 1000})
    series = s["capacity"]["series"]
    assert series and all(len(p) == 2 for p in series)
    assert all(isinstance(t, float) for t, _ in series)
    assert series[0][1] == pytest.approx(50)


def test_snapshot_series_is_empty_when_capacity_unknown():
    s = snap(down_prom(), [log("acme", 100)], {"acme": 1000})
    assert s["capacity"]["series"] == []
