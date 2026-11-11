import os
import stat

import pytest
from fake_oidc import ISSUER, make_oidc
from typer.testing import CliRunner

from ttalkkak import cli
from ttalkkak.auth.device import (
    DeviceLogin,
    load_tokens,
    save_tokens,
    tenant_of,
    valid_access_token,
)
from ttalkkak.client.config import ClientConfig, load_config
from ttalkkak.errors import TtalkkakError


def login(**state):
    http, srv = make_oidc(**state)
    slept = []
    d = DeviceLogin(ISSUER, "ttalkkak-cli", http, sleep=slept.append)
    return d, d.start(), slept, srv


def test_device_flow_succeeds_after_pending():
    d, st, slept, _ = login(pending_polls=2)
    t = d.poll(st)
    assert t.access_token and tenant_of(t.id_claims) == "acme" and len(slept) == 3


def test_slow_down_increases_interval():
    d, st, slept, _ = login(slow_down_once=True)
    d.poll(st)
    assert slept[1] >= slept[0] + 5


def test_device_flow_denied_and_expired():
    d, st, _, _ = login(deny=True)
    with pytest.raises(TtalkkakError, match="거절"):
        d.poll(st)
    d, st, _, _ = login(expire=True)
    with pytest.raises(TtalkkakError, match="만료"):
        d.poll(st)


def test_poll_stops_when_code_lifetime_passes():
    http, _ = make_oidc(pending_polls=10**6)
    clock = [0.0]
    d = DeviceLogin(
        ISSUER,
        "ttalkkak-cli",
        http,
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        now=lambda: clock[0],
    )
    with pytest.raises(TtalkkakError, match="만료"):
        d.poll(d.start())


def test_whoami_requires_exactly_one_tenant():
    for groups in ([], ["a1", "b1"], ["Bad Tenant"]):
        with pytest.raises(TtalkkakError):
            tenant_of({"sub": "u", "groups": groups})
    with pytest.raises(TtalkkakError):
        tenant_of({"sub": "u"})


def test_issuer_mismatch_and_plain_http_are_refused():
    http, _ = make_oidc(issuer="https://evil.test")
    with pytest.raises(TtalkkakError, match="issuer"):
        DeviceLogin(ISSUER, "c", http).start()
    with pytest.raises(TtalkkakError, match="https"):
        DeviceLogin("http://idp.example.com", "c", http)


def test_refresh_and_revoke():
    d, st, _, srv = login()
    t = d.poll(st)
    t2 = d.refresh(t)
    assert t2.access_token != t.access_token and tenant_of(t2.id_claims) == "acme"
    d.revoke(t2)
    assert srv.revoked == [t2.refresh_token]


def test_tokens_file_is_private(tmp_path):
    d, st, _, _ = login()
    p = save_tokens(d.poll(st), tmp_path)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert load_tokens(tmp_path).access_token


def test_valid_access_token_refreshes_near_expiry(tmp_path):
    d, st, _, _ = login()
    t = d.poll(st)
    save_tokens(t, tmp_path)
    cfg = ClientConfig(endpoint="https://gw.test", issuer=ISSUER)
    late = t.expires_at - 30
    assert valid_access_token(cfg, d.http, tmp_path, now=lambda: late) != t.access_token
    assert load_tokens(tmp_path).access_token != t.access_token


def test_cli_login_whoami_logout(tmp_path, monkeypatch):
    http, srv = make_oidc()
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    monkeypatch.setattr(cli, "_http", lambda: http)
    monkeypatch.setattr(cli, "_open_browser", lambda url: None)
    monkeypatch.setattr("ttalkkak.auth.device.time.sleep", lambda s: None)
    r = CliRunner().invoke(
        cli.app, ["login", "--endpoint", "https://gw.test", "--issuer", ISSUER]
    )
    assert r.exit_code == 0, r.output
    assert "ABCD-1234" in r.output and "acme" in r.output
    assert load_config().issuer == ISSUER
    r = CliRunner().invoke(cli.app, ["whoami"])
    assert "u1@acme" in r.output and "acme" in r.output
    r = CliRunner().invoke(cli.app, ["logout"])
    assert r.exit_code == 0 and load_tokens(tmp_path) is None and srv.revoked


def test_cli_login_reads_issuer_from_gateway(tmp_path, monkeypatch):
    oidc_http, _ = make_oidc()

    def handler(req):
        if req.url.path == "/v1/auth-config":
            import httpx

            return httpx.Response(200, json={"issuer": ISSUER, "client_id": "cli2"})
        return oidc_http._transport.handle_request(req)

    import httpx

    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    monkeypatch.setattr(
        cli, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(cli, "_open_browser", lambda url: None)
    monkeypatch.setattr("ttalkkak.auth.device.time.sleep", lambda s: None)
    r = CliRunner().invoke(cli.app, ["login", "--endpoint", "https://gw.test"])
    assert r.exit_code == 0, r.output
    assert load_config().client_id == "cli2"


def _client(handler):
    import httpx

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_server_error_body_becomes_clean_error(tmp_path, monkeypatch):
    import httpx

    _, srv = make_oidc()

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(502, text="<html>bad gateway</html>")
        return srv.handler(req)

    d = DeviceLogin(ISSUER, "c", _client(handler), sleep=lambda s: None)
    with pytest.raises(TtalkkakError, match="502"):
        d.poll(d.start())
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    monkeypatch.setattr(cli, "_http", lambda: _client(handler))
    monkeypatch.setattr(cli, "_open_browser", lambda url: None)
    monkeypatch.setattr("ttalkkak.auth.device.time.sleep", lambda s: None)
    r = CliRunner().invoke(
        cli.app, ["login", "--endpoint", "https://gw.test", "--issuer", ISSUER]
    )
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert "502" in r.output


def test_logout_recovers_from_corrupt_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    (tmp_path / "credentials.json").write_text("{not json")
    assert load_tokens(tmp_path) is None
    r = CliRunner().invoke(cli.app, ["logout"])
    assert r.exit_code == 0 and not (tmp_path / "credentials.json").exists()


def test_plain_http_gateway_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    r = CliRunner().invoke(cli.app, ["login", "--endpoint", "http://gw.example.com"])
    assert r.exit_code == 2 and "https" in r.output


def test_non_object_id_token_claims_are_ignored():
    import base64

    payload = base64.urlsafe_b64encode(b'["a"]').decode().rstrip("=")
    from ttalkkak.auth.device import _claims

    assert _claims(f"h.{payload}.s") == {}


def test_existing_loose_file_is_tightened_before_write(tmp_path):
    from ttalkkak.client.config import write_private

    p = tmp_path / "c.json"
    p.write_text("old")
    os.chmod(p, 0o644)
    modes = []
    real_fdopen = os.fdopen

    def spy(fd, *a, **kw):
        modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, *a, **kw)

    import unittest.mock

    with unittest.mock.patch("os.fdopen", spy):
        write_private(p, "secret")
    assert p.read_text() == "secret" and stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert all(m == 0o600 for m in modes)
