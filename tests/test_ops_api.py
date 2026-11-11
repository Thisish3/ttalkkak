import json
import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ttalkkak.cost.model import CostConfig, CostResult, Prices
from ttalkkak.gateway.manifests import tenants_json
from ttalkkak.ops_api.app import AuthConfig, create_ops_api
from ttalkkak.tenants.store import TenantStore

ISS, CID = "https://tk.example/dex", "ttalkkak-cli"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWKS = {"keys": [{**JWK, "kid": "k1", "alg": "RS256", "use": "sig"}]}
PRICES = Prices(input=1_000_000, cached_input=100_000, output=2_000_000)
COST = CostResult(
    e_in_j=1, e_cached_j=0.1, e_out_j=5, warnings=[], operating_point=None,
    gpus_needed=None, cost_krw_per_mtok=PRICES, price_krw_per_mtok=PRICES,
    config=CostConfig(),
)  # fmt: skip


def token(groups=("acme",), iss=ISS, aud=CID, exp=300, key=KEY):
    claims = {"iss": iss, "aud": aud, "sub": "u1", "email": "u1@acme",
              "groups": list(groups), "exp": int(time.time()) + exp}  # fmt: skip
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def logline(ts, rid, tenant, i=100, c=50, o=10, status=200):
    return json.dumps({
        "ts": ts.isoformat(), "request_id": rid, "tenant": tenant, "model": "m",
        "status": status, "input_tokens": i, "cached_input_tokens": c,
        "output_tokens": o,
    })  # fmt: skip


@pytest.fixture
def env(tmp_path):
    st = TenantStore(tmp_path / "t.db")
    st.add_tenant("acme", "Acme")
    st.set_limit("acme", 6000)
    st.add_tenant("beta", "Beta")
    st.set_cache("beta", "shared")
    raw = st.issue_key("acme")
    now = datetime.now(UTC)
    lines = [
        logline(now, "r1", "acme"),
        logline(now, "r2", "acme", status=503),
        logline(now - timedelta(days=40), "r0", "acme"),
        logline(now, "r3", "beta", i=1, c=0, o=1),
    ]
    app = create_ops_api(
        tenants=lambda: json.loads(tenants_json(st)),
        cost=COST,
        log_source=lambda: lines,
        auth=AuthConfig(issuer=ISS, client_id=CID, jwks=lambda: JWKS),
    )
    return TestClient(app), raw, st


def test_pricing_is_public_estimate(env):
    r = env[0].get("/v1/pricing")
    assert r.status_code == 200 and r.json()["note"] == "estimate"
    assert r.json()["output"] == 2_000_000 and r.json()["per"] == 1_000_000


def test_auth_config_is_public(env):
    assert env[0].get("/v1/auth-config").json() == {"issuer": ISS, "client_id": CID}


def test_authz_accepts_api_key_and_sets_tenant_headers(env):
    c, raw, st = env
    r = c.post("/authz/v1/chat/completions", headers={"authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.headers["x-ttalkkak-tenant"] == "acme"
    assert r.headers["x-ttalkkak-cache-salt"] == st.cache_salt("acme")


def test_authz_accepts_valid_jwt(env):
    r = env[0].post("/authz/x", headers={"authorization": f"Bearer {token()}"})
    assert r.status_code == 200 and r.headers["x-ttalkkak-tenant"] == "acme"
    assert r.headers["x-ttalkkak-subject"] == "u1"


@pytest.mark.parametrize(
    "bearer",
    [
        None,
        "tk_acme_wrong",
        "not-a-jwt",
        "EXPIRED",
        "WRONG_ISS",
        "WRONG_AUD",
        "TWO_GROUPS",
        "OTHER_KEY",
    ],
)
def test_authz_rejects(env, bearer):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    b = {
        "EXPIRED": lambda: token(exp=-10),
        "WRONG_ISS": lambda: token(iss="https://evil"),
        "WRONG_AUD": lambda: token(aud="other"),
        "TWO_GROUPS": lambda: token(groups=("acme", "beta")),
        "OTHER_KEY": lambda: token(key=other),
    }.get(bearer, lambda: bearer)()
    headers = {"authorization": f"Bearer {b}"} if b else {}
    r = env[0].post("/authz/x", headers=headers)
    assert r.status_code == 401 and "x-ttalkkak-tenant" not in r.headers


def test_authz_rejects_unknown_tenant_in_jwt(env):
    r = env[0].post("/authz/x", headers={"authorization": f"Bearer {token(['gone'])}"})
    assert r.status_code == 403


def test_usage_needs_single_tenant_header(env):
    c = env[0]
    assert c.get("/v1/usage").status_code == 400
    assert c.get("/v1/usage", headers={"x-ttalkkak-tenant": "a,b"}).status_code == 400


def test_usage_rejects_multi_tenant_token(env):
    r = env[0].get("/v1/usage", headers={"x-ttalkkak-tenant": "acme,beta"})
    assert r.status_code == 400


def test_usage_sums_today_and_month(env):
    r = env[0].get("/v1/usage", headers={"x-ttalkkak-tenant": "acme"}).json()
    assert r["today"]["output_tokens"] == 10 and r["today"]["requests"] == 1
    # (100-50)*1 + 50*0.1 + 10*2 = 75 KRW
    assert r["today"]["amount_krw"] == 75 and r["month"]["amount_krw"] >= 75


def test_metrics_have_tenant_labels(env):
    text = env[0].get("/metrics").text
    assert 'ttalkkak_tokens_total{tenant="acme",model="m",kind="output"} 20' in text
    assert 'ttalkkak_tokens_total{tenant="acme",model="m",kind="total"}' in text
    assert 'ttalkkak_requests_total{tenant="acme",status="503"} 1' in text
    assert 'ttalkkak_tenant_limit_tokens_per_minute{tenant="acme"} 6000' in text


def test_build_serve_app_from_files(tmp_path, env):
    from ttalkkak.ops_api.app import build_serve_app

    _, raw, st = env
    (tmp_path / "tenants.json").write_text(tenants_json(st))
    (tmp_path / "cost.json").write_text(COST.model_dump_json())
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.jsonl").write_text(
        logline(datetime.now(UTC), "r9", "acme") + "\n"
    )
    app = build_serve_app(
        tenants_path=tmp_path / "tenants.json",
        cost_path=tmp_path / "cost.json",
        logs=tmp_path / "logs",
        issuer=ISS,
        client_id=CID,
        jwks_url="https://unused.test/keys",
    )
    c = TestClient(app)
    assert c.get("/v1/pricing").json()["output"] == 2_000_000
    r = c.post("/authz/x", headers={"authorization": f"Bearer {raw}"})
    assert r.headers["x-ttalkkak-tenant"] == "acme"
    assert 'kind="output"} 10' in c.get("/metrics").text


def test_stack_includes_ops_serve(tmp_path):
    from pathlib import Path

    import yaml

    from ttalkkak.gateway.manifests import GatewayOptions, render_stack

    st = TenantStore(tmp_path / "t.db")
    run = Path(__file__).parent / "fixtures/runs/vllm-nvidia"
    files = render_stack(run, st, COST, GatewayOptions())
    docs = list(yaml.safe_load_all(files["25-ops-serve.yaml"]))
    (dep,) = [d for d in docs if d["kind"] == "Deployment"]
    args = dep["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[:2] == ["ops", "serve"] and "--logs-from-cluster" in args
    (cm,) = [d for d in docs if d["kind"] == "ConfigMap"]
    assert json.loads(cm["data"]["cost.json"])["price_krw_per_mtok"]["output"] == 2e6


def test_authz_accepts_jwt_for_tenant_without_keys_or_limits(env):
    r = env[0].post("/authz/x", headers={"authorization": f"Bearer {token(['beta'])}"})
    assert r.status_code == 200 and r.headers["x-ttalkkak-tenant"] == "beta"
