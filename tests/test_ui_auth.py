import stat

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ttalkkak import ops_cli
from ttalkkak.errors import TtalkkakError
from ttalkkak.ui import auth
from ttalkkak.ui.app import UiDeps, create_app

TAURI = "tauri://localhost"


def deps():
    return UiDeps(list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None)


@pytest.fixture
def token_path(tmp_path):
    p = tmp_path / "home" / "console-token"
    auth.save_token(p, auth.new_token())
    return p


@pytest.fixture
def remote(tmp_path, token_path):
    return TestClient(create_app(tmp_path, deps(), token_path=token_path))


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_remote_requires_token(remote, token_path):
    token = auth.load_token(token_path)
    for headers in ({}, bearer("ttk_op_wrong"), {"Authorization": token}):
        r = remote.get("/api/gpu", headers=headers)
        assert r.status_code == 401
        assert r.json() == {"error": "운영자 토큰이 필요합니다"}
    assert remote.get("/api/ping").status_code == 401
    assert remote.get("/api/gpu", headers=bearer(token)).status_code == 200


def test_remote_accepts_any_host(remote, token_path):
    r = remote.get(
        "/api/gpu", headers={**bearer(auth.load_token(token_path)), "Host": "100.1.2.3"}
    )
    assert r.status_code == 200


def test_local_mode_unchanged(tmp_path):
    c = TestClient(create_app(tmp_path, deps()), base_url="http://127.0.0.1:8787")
    assert c.get("/api/gpu").status_code == 200
    assert c.get("/api/gpu", headers={"Host": "evil.example"}).status_code == 403
    assert (
        c.options("/api/gpu", headers={"Origin": TAURI}).headers.get(
            "access-control-allow-origin"
        )
        is None
    )


def test_cors_only_tauri_origins(remote, token_path):
    h = bearer(auth.load_token(token_path))
    for origin in (
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ):
        pre = remote.options(
            "/api/gpu",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-origin"] == origin
        assert "authorization" in pre.headers["access-control-allow-headers"].lower()
        assert "access-control-allow-credentials" not in pre.headers
        ok = remote.get("/api/gpu", headers={**h, "Origin": origin})
        assert ok.headers["access-control-allow-origin"] == origin
    bad = remote.options(
        "/api/gpu",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert bad.status_code == 403
    assert "access-control-allow-origin" not in bad.headers
    other = remote.get("/api/gpu", headers={**h, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    denied = remote.get("/api/gpu", headers={"Origin": TAURI})
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == TAURI


def test_token_file_private_mode(tmp_path):
    p = tmp_path / "h" / "console-token"
    t = auth.new_token()
    auth.save_token(p, t)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert auth.load_token(p) == t
    assert t.startswith("ttk_op_") and len(t) >= len("ttk_op_") + 43


def test_check_is_exact_bearer():
    assert auth.check("Bearer abc", "abc")
    assert not auth.check("Bearer abcd", "abc")
    assert not auth.check("abc", "abc")
    assert not auth.check(None, "abc")


def test_ping_payload(tmp_path, token_path):
    from ttalkkak.chips import chip_by_name

    d = deps()
    d.chip = chip_by_name("nvidia-gpu")
    c = TestClient(create_app(tmp_path, d, token_path=token_path))
    r = c.get("/api/ping", headers=bearer(auth.load_token(token_path)))
    body = r.json()
    assert set(body) == {"product", "version", "chip", "max_replicas"}
    assert body["product"] == "ttalkkak" and body["chip"] == "nvidia-gpu"
    import importlib.metadata

    assert body["version"] == importlib.metadata.version("ttalkkak")


def test_ping_max_replicas_from_configmap(tmp_path, token_path):
    import json
    import subprocess

    from ttalkkak.chips import chip_by_name

    d = deps()
    d.chip = chip_by_name("nvidia-gpu")
    d.kubectl = lambda args: subprocess.CompletedProcess(
        args, 0, json.dumps({"data": {"max_replicas": "3", "deployment": "v"}}), ""
    )
    c = TestClient(create_app(tmp_path, d, token_path=token_path))
    r = c.get("/api/ping", headers=bearer(auth.load_token(token_path)))
    assert r.json()["max_replicas"] == 3


def test_revoke_rejects_old_token_on_running_server(remote, token_path, monkeypatch):
    old = auth.load_token(token_path)
    assert remote.get("/api/gpu", headers=bearer(old)).status_code == 200
    monkeypatch.setattr(ops_cli, "TK_HOME", token_path.parent)
    r = CliRunner().invoke(ops_cli.ops, ["token", "revoke"])
    assert r.exit_code == 0 and not token_path.exists()
    denied = remote.get("/api/gpu", headers=bearer(old))
    assert denied.status_code == 401
    assert denied.json() == {"error": "운영자 토큰이 필요합니다"}
    new = auth.new_token()
    auth.save_token(token_path, new)
    assert remote.get("/api/gpu", headers=bearer(old)).status_code == 401
    assert remote.get("/api/gpu", headers=bearer(new)).status_code == 200


def test_401_same_with_and_without_token_file(tmp_path):
    missing = tmp_path / "none"
    c = TestClient(create_app(tmp_path, deps(), token_path=missing))
    r = c.get("/api/gpu", headers=bearer("ttk_op_x"))
    assert (r.status_code, r.json()) == (401, {"error": "운영자 토큰이 필요합니다"})


def test_token_cli_new_show(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_cli, "TK_HOME", tmp_path)
    run = CliRunner()
    assert run.invoke(ops_cli.ops, ["token", "show"]).exit_code == 1
    new = run.invoke(ops_cli.ops, ["token", "new"])
    shown = run.invoke(ops_cli.ops, ["token", "show"])
    assert new.exit_code == 0 and shown.exit_code == 0
    assert shown.stdout.strip() == auth.load_token(tmp_path / "console-token")
    assert shown.stdout.strip() in new.stdout


def test_remote_without_token_file_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_cli, "TK_HOME", tmp_path)
    r = CliRunner().invoke(ops_cli.ops, ["ui", "--remote"])
    assert r.exit_code == 2
    assert "ttalkkak ops token new" in r.output


def test_remote_host_tailnet_default():
    assert auth.remote_host(None, lambda: "100.64.0.7") == "100.64.0.7"


def test_remote_host_without_tailscale_refuses():
    with pytest.raises(TtalkkakError, match="tailscale"):
        auth.remote_host(None, lambda: None)


def test_remote_host_explicit_honored():
    def boom():
        raise AssertionError("must not look up tailscale")

    assert auth.remote_host("0.0.0.0", boom) == "0.0.0.0"


def test_tailscale_ipv4_parses_first_line(monkeypatch):
    import subprocess

    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "100.64.0.7\n100.64.0.8\n", "")

    monkeypatch.setattr(subprocess, "run", fake)
    assert auth.tailscale_ipv4() == "100.64.0.7"

    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert auth.tailscale_ipv4() is None


@pytest.fixture
def served(tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.setattr(ops_cli, "TK_HOME", tmp_path)
    auth.save_token(tmp_path / "console-token", auth.new_token())
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    return seen


def test_cli_remote_binds_tailnet_ip_and_warns(served, monkeypatch):
    monkeypatch.setattr(ops_cli, "_tailnet_lookup", lambda: "100.64.0.7")
    r = CliRunner().invoke(ops_cli.ops, ["ui", "--remote"])
    assert r.exit_code == 0, r.output
    assert served["host"] == "100.64.0.7"
    assert "평문 HTTP" in r.output


def test_cli_remote_no_tailscale_refuses(served, monkeypatch):
    monkeypatch.setattr(ops_cli, "_tailnet_lookup", lambda: None)
    r = CliRunner().invoke(ops_cli.ops, ["ui", "--remote"])
    assert r.exit_code == 2 and served == {}
    assert "--host" in r.output


def test_cli_remote_explicit_host(served, monkeypatch):
    monkeypatch.setattr(ops_cli, "_tailnet_lookup", lambda: None)
    r = CliRunner().invoke(ops_cli.ops, ["ui", "--remote", "--host", "0.0.0.0"])
    assert r.exit_code == 0, r.output
    assert served["host"] == "0.0.0.0"


# ---- fix round 1 ----


def _is_guarded(fn):
    return fn.__qualname__.endswith("guarded.<locals>.handler")


def test_every_ops_route_is_guarded_exactly_once(tmp_path):
    from ttalkkak.ui.app import _BadRequest, _body
    from ttalkkak.ui.ops_routes import ops_routes

    for route in ops_routes(tmp_path, deps(), _body, _BadRequest):
        fn = route.endpoint
        assert _is_guarded(fn), route.path
        inner = [c.cell_contents for c in fn.__closure__ or ()]
        assert not any(callable(x) and _is_guarded(x) for x in inner), route.path


def test_live_error_is_guarded_json(tmp_path, monkeypatch):
    from ttalkkak.ui import ops_routes

    def boom(*a, **k):
        raise TtalkkakError("스냅샷 실패", 503)

    monkeypatch.setattr(ops_routes, "build_snapshot", boom)
    c = TestClient(create_app(tmp_path, deps()), base_url="http://127.0.0.1")
    r = c.get("/api/live")
    assert r.status_code == 503
    assert r.json() == {"error": "스냅샷 실패"}


class FakeProm:
    def instant(self, q):
        return [({"pod": "vllm-1", "model_name": "m"}, 1.0)]

    range = instant


def _ping(tmp_path, token_path, d):
    c = TestClient(create_app(tmp_path, d, token_path=token_path))
    return c.get("/api/ping", headers=bearer(auth.load_token(token_path)))


def test_ping_max_replicas_falls_back_to_running_pods(tmp_path, token_path):
    d = deps()
    d.prometheus = FakeProm()
    d.cluster_status = lambda: [
        {"name": "vllm-1", "ready": "1/1", "restarts": 0, "phase": "Running"},
        {"name": "other", "ready": "1/1", "restarts": 0, "phase": "Running"},
    ]
    assert _ping(tmp_path, token_path, d).json()["max_replicas"] == 1


def test_ping_max_replicas_null_when_nothing_readable(tmp_path, token_path):
    assert _ping(tmp_path, token_path, deps()).json()["max_replicas"] is None


def test_ping_version_matches_package(tmp_path, token_path):
    import importlib.metadata

    body = _ping(tmp_path, token_path, deps()).json()
    assert body["version"] == importlib.metadata.version("ttalkkak")


def test_ping_version_unknown_when_not_installed(tmp_path, token_path, monkeypatch):
    import importlib.metadata

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    r = _ping(tmp_path, token_path, deps())
    assert r.status_code == 200 and r.json()["version"] == "unknown"


def test_save_token_replaces_symlink_not_follows(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    p = tmp_path / "home" / "console-token"
    p.parent.mkdir()
    p.symlink_to(victim)
    auth.save_token(p, "ttk_op_x")
    assert victim.read_text() == "keep"
    assert not p.is_symlink() and p.read_text().strip() == "ttk_op_x"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert [f.name for f in p.parent.iterdir()] == ["console-token"]


def test_preflight_rejection_body_is_korean(remote):
    r = remote.options("/api/gpu", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"error": "허용되지 않은 출처입니다"}


def test_vary_origin_whenever_origin_seen(remote, token_path):
    h = bearer(auth.load_token(token_path))
    for resp in (
        remote.get("/api/gpu", headers={**h, "Origin": "https://evil.example"}),
        remote.get("/api/gpu", headers={**h, "Origin": TAURI}),
        remote.get("/api/gpu", headers={"Origin": "https://evil.example"}),
        remote.options("/api/gpu", headers={"Origin": "https://evil.example"}),
    ):
        assert "Origin" in resp.headers["vary"]


def test_websocket_scope_needs_token(tmp_path, token_path):
    import asyncio

    from ttalkkak.ui.app import _RemoteToken

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    mw = _RemoteToken(inner, token_path)

    def run(headers):
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {"type": "websocket.connect"}

        scope = {"type": "websocket", "path": "/api/ws", "headers": headers}
        asyncio.run(mw(scope, receive, send))
        return sent

    sent = run([])
    assert reached == [] and sent[0]["type"] == "websocket.close"
    token = auth.load_token(token_path)
    run([(b"authorization", f"Bearer {token}".encode())])
    assert reached == ["websocket"]
