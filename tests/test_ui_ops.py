import json
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from ttalkkak.cost.model import CostConfig, CostResult, Prices
from ttalkkak.tenants.store import TenantStore
from ttalkkak.ui.app import UiDeps, create_app

P = Prices(input=1_000_000, cached_input=100_000, output=2_000_000)
LOG = json.dumps({
    "ts": datetime.now(UTC).isoformat(), "request_id": "r1", "tenant": "acme",
    "model": "m", "status": 200, "input_tokens": 100, "cached_input_tokens": 50,
    "output_tokens": 10,
})  # fmt: skip


@pytest.fixture
def c(tmp_path):
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    cost = CostResult(
        e_in_j=1, e_cached_j=0.1, e_out_j=5, warnings=["w"], operating_point=None,
        gpus_needed=None, cost_krw_per_mtok=P, price_krw_per_mtok=P, config=CostConfig(),
    )  # fmt: skip
    (tmp_path / "runs" / "r1" / "cost.json").write_text(cost.model_dump_json())
    deps = UiDeps(
        list_models=list,
        detect_gpu=lambda: None,
        run_fn=lambda *a: None,
        store=TenantStore(tmp_path / "t.db"),
        cluster_status=lambda: [
            {"name": "vllm-1", "ready": "1/1", "restarts": 0, "phase": "Running"}
        ],
        gateway_logs=lambda: [LOG],
    )
    return TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))


def post(c, url, body=None):
    return c.post(url, json=body or {})


def test_tenant_lifecycle_never_lists_raw_keys(c):
    assert (
        post(c, "/api/ops/tenants", {"id": "acme", "name": "Acme"}).status_code == 200
    )
    raw = post(c, "/api/ops/tenants/acme/keys").json()["key"]
    assert raw.startswith("tk_acme_")
    listing = c.get("/api/ops/tenants").text
    assert raw not in listing and raw[: len("tk_acme_") + 8] in listing
    assert post(c, "/api/ops/tenants/acme/limit", {"per_min": 500}).status_code == 200
    assert post(c, "/api/ops/tenants/acme/cache", {"mode": "shared"}).status_code == 200
    t = c.get("/api/ops/tenants").json()[0]
    assert t["token_limit_per_min"] == 500 and t["cache_mode"] == "shared"
    prefix = raw[: len("tk_acme_") + 8]
    assert post(c, f"/api/ops/keys/{prefix}/revoke").status_code == 200
    assert c.get("/api/ops/tenants").json()[0]["keys"] == []


def test_bad_tenant_input_is_400(c):
    assert post(c, "/api/ops/tenants", {"id": "Bad Id", "name": "x"}).status_code == 400
    assert post(c, "/api/ops/tenants/none/keys").status_code == 400


def test_cost_and_status(c):
    assert c.get("/api/ops/cost?run=r1").json()["warnings"] == ["w"]
    assert c.get("/api/ops/cost?run=missing").status_code == 404
    assert c.get("/api/ops/cost?run=../x").status_code == 400
    assert c.get("/api/ops/status").json()["pods"][0]["name"] == "vllm-1"


def test_usage_and_invoice_csv(c):
    today = datetime.now(UTC).date()
    q = f"from={today}&to={today.replace(day=28) if today.day < 28 else today}"
    q = f"from={today}&to=2099-01-01&run=r1"
    rows = c.get(f"/api/ops/usage?{q}").json()
    assert rows[0]["tenant"] == "acme" and rows[0]["amount_krw"] == 75
    csv = c.get(f"/api/ops/invoice.csv?{q}")
    assert csv.headers["content-type"].startswith("text/csv") and "acme" in csv.text


def test_ops_endpoints_keep_local_only_guard(c):
    r = c.post("/api/ops/tenants", content="{}", headers={"content-type": "text/plain"})
    assert r.status_code == 415


class Kc:
    def __init__(self):
        self.calls = []

    def __call__(self, args, input=None):
        self.calls.append(args)
        from types import SimpleNamespace

        out = ""
        if args[:2] == ["get", "configmap"]:
            out = json.dumps({"data": {
                "deployment": "vllm-qwen", "max_replicas": "3", "enabled": "true",
            }})  # fmt: skip
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


@pytest.fixture
def cc(tmp_path):
    kc = Kc()
    deps = UiDeps(
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None,
        cluster_status=lambda: [
            {"name": "vllm-qwen-abc", "ready": "1/1", "restarts": 0, "phase": "Running"},
            {"name": "gateway-xyz", "ready": "1/1", "restarts": 0, "phase": "Running"},
        ],
        kubectl=kc,
    )  # fmt: skip
    return TestClient(create_app(tmp_path, deps, frozenset({"testserver"}))), kc


def test_scale_route_bounds(cc):
    c, kc = cc
    assert post(c, "/api/replicas", {"count": 0}).status_code == 400
    assert post(c, "/api/replicas", {"count": 4}).status_code == 400
    assert post(c, "/api/replicas", {"count": "x"}).status_code == 400
    assert post(c, "/api/replicas", {"count": 2}).status_code == 200
    assert [
        "scale", "deployment/vllm-qwen", "--replicas=2", "-n", "ttalkkak"
    ] in kc.calls  # fmt: skip


def test_delete_pod_rejects_foreign_name(cc):
    c, kc = cc
    assert post(c, "/api/replicas/gateway-xyz/delete").status_code == 400
    assert post(c, "/api/replicas/Bad_Name/delete").status_code in (400, 404)
    assert post(c, "/api/replicas/vllm-qwen-abc/delete").status_code == 200
    assert ["delete", "pod", "vllm-qwen-abc", "-n", "ttalkkak"] in kc.calls
    assert not any("gateway-xyz" in a for a in kc.calls)


def test_loadtest_routes_need_gateway(cc):
    c, _ = cc
    assert post(c, "/api/loadtest", {"max_users": 5}).status_code == 503
    assert c.get("/api/loadtests").json() == []
    assert c.get("/api/loadtest/nope").status_code == 404


def test_rejected_second_loadtest_keeps_running_model(tmp_path):
    from ttalkkak.live import loadtest as lt

    kc = Kc()
    deps = UiDeps(
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None,
        store=TenantStore(tmp_path / "t.db"),
        cluster_status=lambda: [
            {"name": "vllm-qwen-abc", "ready": "1/1", "restarts": 0, "phase": "Running"}
        ],
        kubectl=kc, gateway_url="http://gw/v1",
        prometheus=type("P", (), {"instant": lambda s, q: [({"model_name": "new"}, 1)]})(),
    )  # fmt: skip
    c = TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))
    runner = None
    orig = lt.LoadTestRunner.__init__

    def spy(self, *a, **k):
        nonlocal runner
        orig(self, *a, **k)
        runner = self
        self._active = "busy"
        self.model = "old"

    lt.LoadTestRunner.__init__ = spy
    try:
        r = post(c, "/api/loadtest", {"max_users": 5, "autoscale_on": True})
    finally:
        lt.LoadTestRunner.__init__ = orig
    assert r.status_code == 409 and runner.model == "old"
    assert post(c, "/api/loadtest", {"max_users": "x"}).status_code == 400


GOOD = (
    '{"t":"2026-11-16T12:00:00Z","action":"up","from_replicas":1,'
    '"to_replicas":2,"reason":"r"}'
)


class LogKc(Kc):
    def __init__(self, logs):
        super().__init__()
        self.logs = logs

    def __call__(self, args, input=None):
        from types import SimpleNamespace

        if args[0] == "logs":
            self.calls.append(args)
            return SimpleNamespace(returncode=0, stdout=self.logs, stderr="")
        return super().__call__(args, input)


def test_autoscale_toggle_patches_configmap(cc):
    c, kc = cc
    assert post(c, "/api/autoscale", {"enabled": "yes"}).status_code == 400
    assert post(c, "/api/autoscale", {"enabled": False}).status_code == 200
    patch = [a for a in kc.calls if a[0] == "patch"][-1]
    assert patch[:4] == ["patch", "configmap", "ttalkkak-autoscale", "-n"]
    assert json.loads(patch[patch.index("-p") + 1]) == {"data": {"enabled": "false"}}


def test_decisions_parsed_and_appended_once(tmp_path):
    kc = LogKc(f"noise\nAUTOSCALE {GOOD}\nAUTOSCALE {{bad\n")
    deps = UiDeps(
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None,
        cluster_status=list, kubectl=kc,
    )  # fmt: skip
    c = TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))
    for _ in range(2):
        rows = c.get("/api/autoscale/decisions").json()
        assert [r["action"] for r in rows] == ["up"]
    assert ["logs", "deploy/ttalkkak-ops", "-n", "ttalkkak", "--since=24h"] in kc.calls
    f = tmp_path / "runs/autoscale/2026-11-16.jsonl"
    assert len(f.read_text().splitlines()) == 1
    snap = c.get("/api/live").json()
    assert [d["action"] for d in snap["autoscale"]["decisions"]] == ["up"]


def test_up_decision_gets_ready_after_s(tmp_path):
    from types import SimpleNamespace

    def pod(name, at):
        return {
            "metadata": {"name": name},
            "status": {"conditions": [
                {"type": "Ready", "status": "True", "lastTransitionTime": at}
            ]},
        }  # fmt: skip

    class K(LogKc):
        def __call__(self, args, input=None):
            if args[:2] == ["get", "pods"]:
                items = [
                    pod("vllm-qwen-a", "2026-11-16T11:00:00Z"),
                    pod("vllm-qwen-b", "2026-11-16T12:01:30Z"),
                ]
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps({"items": items}), stderr=""
                )
            return super().__call__(args, input)

    deps = UiDeps(
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None,
        cluster_status=list, kubectl=K(f"AUTOSCALE {GOOD}\n"),
    )  # fmt: skip
    c = TestClient(create_app(tmp_path, deps, frozenset({"testserver"})))
    assert c.get("/api/autoscale/decisions").json()[0]["ready_after_s"] == 90
    line = (tmp_path / "runs/autoscale/2026-11-16.jsonl").read_text()
    assert json.loads(line)["ready_after_s"] == 90
