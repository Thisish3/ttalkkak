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
