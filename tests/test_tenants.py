import os
import stat
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from ttalkkak.cli import app
from ttalkkak.errors import TtalkkakError
from ttalkkak.tenants.store import TenantStore


@pytest.fixture
def st(tmp_path):
    s = TenantStore(tmp_path / "tenants.db")
    s.add_tenant("acme", "Acme")
    return s


def test_db_is_private(tmp_path, st):
    assert stat.S_IMODE(os.stat(tmp_path / "tenants.db").st_mode) == 0o600


def test_raw_key_is_never_stored(tmp_path, st):
    raw = st.issue_key("acme")
    assert raw.startswith("tk_acme_")
    assert raw.encode() not in (tmp_path / "tenants.db").read_bytes()
    assert st.resolve(raw) == "acme" and st.resolve(raw + "x") is None


def test_rotation_keeps_old_key_for_grace_period(st):
    old = st.issue_key("acme")
    new = st.rotate_key("acme", grace_hours=24)
    now = datetime.now(UTC)
    assert st.resolve(old, now) == "acme" and st.resolve(new, now) == "acme"
    later = now + timedelta(hours=25)
    assert st.resolve(old, later) is None and st.resolve(new, later) == "acme"


def test_revoke_is_immediate(st):
    raw = st.issue_key("acme")
    st.revoke_key(raw[: len("tk_acme_") + 8])
    assert st.resolve(raw) is None


def test_cache_salt_isolated_by_default_and_rotates(st):
    s1 = st.cache_salt("acme")
    assert s1 and len(s1) == 32
    st.rotate_salt("acme")
    assert st.cache_salt("acme") != s1
    st.set_cache("acme", "shared")
    assert st.cache_salt("acme") is None


def test_bad_tenant_id_and_unknown_tenant(st):
    with pytest.raises(TtalkkakError):
        st.add_tenant("Bad Id", "x")
    with pytest.raises(TtalkkakError):
        st.issue_key("nobody")
    with pytest.raises(TtalkkakError):
        st.add_tenant("acme", "dup")


def test_cli_issue_prints_key_once_and_list_shows_table(tmp_path):
    db = str(tmp_path / "t.db")
    r = CliRunner().invoke(
        app, ["ops", "tenants", "add", "acme", "--name", "Acme", "--db", db]
    )
    assert r.exit_code == 0
    r = CliRunner().invoke(app, ["ops", "tenants", "key-issue", "acme", "--db", db])
    assert (
        r.exit_code == 0
        and "tk_acme_" in r.output
        and "다시 볼 수 없습니다" in r.output
    )
    r = CliRunner().invoke(app, ["ops", "tenants", "list", "--db", db])
    assert "acme" in r.output and "isolated" in r.output and "tk_acme_" not in r.output
