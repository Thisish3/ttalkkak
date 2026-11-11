import json
from pathlib import Path

import pytest
import yaml

from ttalkkak.gateway.manifests import GatewayOptions, render_stack
from ttalkkak.tenants.store import TenantStore

RUN = Path(__file__).parent / "fixtures/runs/vllm-nvidia"


@pytest.fixture
def store(tmp_path):
    st = TenantStore(tmp_path / "t.db")
    st.add_tenant("acme", "Acme")
    st.set_limit("acme", 6000)
    st.add_tenant("beta", "Beta")
    st.set_cache("beta", "shared")
    st.raw = st.issue_key("acme")
    return st


def docs(store, **kw):
    files = render_stack(RUN, store, None, GatewayOptions(**kw))
    return files, {
        n: list(yaml.safe_load_all(t)) for n, t in files.items() if n.endswith(".yaml")
    }


def kind(d, name, k):
    return [x for x in d[name] if x["kind"] == k]


def test_files_and_order(store):
    files, d = docs(store)
    assert sorted(d) == [
        "00-namespace.yaml",
        "10-redis.yaml",
        "15-dex.yaml",
        "20-vllm.yaml",
        "25-ops-serve.yaml",
        "30-llmd.yaml",
        "40-gateway.yaml",
        "50-observability.yaml",
        "alerts.yaml",
    ]
    assert "README.md" in files and "grafana/usage.json" in files


def test_vllm_replicas_usage_flag_and_scrape(store):
    (dep,) = kind(docs(store, replicas=2)[1], "20-vllm.yaml", "Deployment")
    assert dep["spec"]["replicas"] == 2
    assert (
        "--enable-prompt-tokens-details"
        in dep["spec"]["template"]["spec"]["containers"][0]["args"]
    )
    assert dep["spec"]["template"]["metadata"]["annotations"]["prometheus.io/scrape"]


def test_pool_selects_vllm_pods(store):
    d = docs(store)[1]
    (pool,) = kind(d, "30-llmd.yaml", "InferencePool")
    (dep,) = kind(d, "20-vllm.yaml", "Deployment")
    labels = dep["spec"]["template"]["metadata"]["labels"]
    assert pool["spec"]["selector"]["matchLabels"].items() <= labels.items()
    (epp,) = kind(d, "30-llmd.yaml", "Deployment")
    args = epp["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[args.index("--pool-name") + 1] == pool["metadata"]["name"]


def test_gateway_records_token_costs_and_limits_per_tenant(store):
    gw = docs(store)[1]["40-gateway.yaml"]
    kinds = {x["kind"] for x in gw}
    assert {
        "GatewayClass",
        "Gateway",
        "AIGatewayRoute",
        "SecurityPolicy",
        "BackendTrafficPolicy",
        "EnvoyExtensionPolicy",
        "EnvoyProxy",
    } <= kinds
    (route,) = [x for x in gw if x["kind"] == "AIGatewayRoute"]
    costs = {c["metadataKey"]: c["type"] for c in route["spec"]["llmRequestCosts"]}
    assert costs == {
        "input_tokens": "InputToken",
        "cached_input_tokens": "CachedInputToken",
        "output_tokens": "OutputToken",
        "total_tokens": "TotalToken",
    }
    (btp,) = [x for x in gw if x["kind"] == "BackendTrafficPolicy"]
    rules = btp["spec"]["rateLimit"]["global"]["rules"]
    acme = [
        r for r in rules if r["clientSelectors"][0]["headers"][0]["value"] == "acme"
    ]
    assert acme and acme[0]["limit"] == {"requests": 6000, "unit": "Minute"}
    assert not [
        r for r in rules if r["clientSelectors"][0]["headers"][0]["value"] == "beta"
    ]


def test_auth_goes_through_ext_auth_and_strips_spoofed_headers(store):
    gw = docs(store)[1]["40-gateway.yaml"]
    (sp,) = [x for x in gw if x["kind"] == "SecurityPolicy"]
    ext = sp["spec"]["extAuth"]
    assert set(ext["http"]["headersToBackend"]) == {
        "x-ttalkkak-tenant",
        "x-ttalkkak-subject",
        "x-ttalkkak-cache-salt",
    }
    assert ext.get("failOpen") is not True


def test_access_log_has_tenant_and_tokens_but_no_body(store):
    gw = docs(store)[1]["40-gateway.yaml"]
    (ep,) = [x for x in gw if x["kind"] == "EnvoyProxy"]
    fmt = json.dumps(ep["spec"]["telemetry"]["accessLog"])
    for f in ("request_id", "tenant", "input_tokens", "cached_input_tokens"):
        assert f in fmt
    assert "REQ_BODY" not in fmt and "RESP_BODY" not in fmt


def test_tenant_secret_holds_hashes_and_salts_only(store):
    files, d = docs(store)
    assert store.raw not in "\n".join(files.values())
    (sec,) = [x for x in d["00-namespace.yaml"] if x["kind"] == "Secret"]
    data = json.loads(sec["stringData"]["tenants.json"])
    assert set(data["keys"].values()) == {"acme"}
    assert data["salts"]["acme"] and "beta" not in data["salts"]


def test_external_issuer_skips_dex(store):
    d = docs(store, issuer="https://idp.example.com")[1]
    assert "15-dex.yaml" not in d


def test_dex_has_public_cli_client_and_https_issuer(store, tmp_path):
    d = docs(store, hostname="tk.example")[1]
    (sec,) = kind(d, "15-dex.yaml", "Secret")
    cfg = yaml.safe_load(sec["stringData"]["config.yaml"])
    assert cfg["issuer"] == "https://tk.example/dex"
    (client,) = cfg["staticClients"]
    assert client["id"] == "ttalkkak-cli" and client["public"] is True


def test_cli_user_add_and_export_gateway(tmp_path, monkeypatch):
    import os
    import shutil
    import stat

    import bcrypt
    from typer.testing import CliRunner

    from ttalkkak import ops_cli
    from ttalkkak.cli import app

    monkeypatch.setattr(ops_cli, "ROOT", tmp_path)
    monkeypatch.setattr(ops_cli, "TK_HOME", tmp_path / ".tk")
    shutil.copytree(RUN, tmp_path / "runs" / "r1")
    db = str(tmp_path / "t.db")
    run = CliRunner().invoke
    assert (
        run(app, ["ops", "tenants", "add", "acme", "--name", "A", "--db", db]).exit_code
        == 0
    )
    r = run(
        app,
        [
            "ops",
            "tenants",
            "user-add",
            "acme",
            "u1@acme",
            "--password-stdin",
            "--db",
            db,
        ],
        input="pw-123456\n",
    )
    assert r.exit_code == 0, r.output
    users = tmp_path / ".tk" / "dex-users.yaml"
    assert stat.S_IMODE(os.stat(users).st_mode) == 0o600
    u = yaml.safe_load(users.read_text())[0]
    assert u["groups"] == ["acme"] and bcrypt.checkpw(b"pw-123456", u["hash"].encode())
    r = run(
        app,
        ["ops", "tenants", "user-add", "nobody", "x@y", "--password-stdin", "--db", db],
        input="pw\n",
    )
    assert r.exit_code != 0
    out = tmp_path / "deploy"
    r = run(
        app,
        [
            "ops",
            "export",
            "--target",
            "gateway",
            "--run",
            "r1",
            "--out",
            str(out),
            "--db",
            db,
        ],
    )
    assert r.exit_code == 0, r.output
    dex = (out / "15-dex.yaml").read_text()
    assert "u1@acme" in dex and "pw-123456" not in dex
    r = run(app, ["ops", "tenants", "user-remove", "u1@acme", "--db", db])
    assert r.exit_code == 0 and yaml.safe_load(users.read_text()) == []


def test_dex_users_live_in_a_secret_and_ops_serve_is_fenced(store):
    import bcrypt

    h = bcrypt.hashpw(b"pw-123456", bcrypt.gensalt()).decode()
    d = docs(store, dex_users=[{"email": "u@a", "hash": h, "groups": ["acme"]}])[1]
    assert not [x for x in d["15-dex.yaml"] if x["kind"] == "ConfigMap"]
    (sec,) = [x for x in d["15-dex.yaml"] if x["kind"] == "Secret"]
    assert h in sec["stringData"]["config.yaml"]
    (np,) = [x for x in d["25-ops-serve.yaml"] if x["kind"] == "NetworkPolicy"]
    allowed = json.dumps(np["spec"]["ingress"])
    assert "envoy-gateway-system" in allowed and "prometheus" in allowed
