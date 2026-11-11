import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ttalkkak.errors import TtalkkakError

TENANT_ID = re.compile(r"^[a-z0-9-]{2,32}$")
CACHE_MODES = ("isolated", "shared")
SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
  token_limit_per_min INTEGER, cache_mode TEXT NOT NULL DEFAULT 'isolated',
  salt_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS keys(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL REFERENCES tenants(id),
  prefix TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
  expires_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Tenant:
    id: str
    name: str
    token_limit_per_min: int | None
    cache_mode: str
    salt_version: int


@dataclass
class KeyRow:
    id: int
    tenant_id: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class TenantStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('salt_secret', ?)",
                (secrets.token_hex(32),),
            )
        (self._salt_secret,) = self.db.execute(
            "SELECT value FROM meta WHERE key = 'salt_secret'"
        ).fetchone()

    def _tenant(self, tid: str) -> Tenant:
        row = self.db.execute(
            "SELECT id, name, token_limit_per_min, cache_mode, salt_version"
            " FROM tenants WHERE id = ?",
            (tid,),
        ).fetchone()
        if row is None:
            raise TtalkkakError(f"테넌트 '{tid}'가 없습니다")
        return Tenant(*row)

    def add_tenant(self, tid: str, name: str) -> Tenant:
        if not TENANT_ID.match(tid):
            raise TtalkkakError("테넌트 id는 소문자·숫자·하이픈 2~32자여야 합니다", 2)
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
                    (tid, name, _now().isoformat()),
                )
        except sqlite3.IntegrityError as err:
            raise TtalkkakError(f"테넌트 '{tid}'가 이미 있습니다", 2) from err
        return self._tenant(tid)

    def list_tenants(self) -> list[Tenant]:
        rows = self.db.execute(
            "SELECT id, name, token_limit_per_min, cache_mode, salt_version"
            " FROM tenants ORDER BY id"
        ).fetchall()
        return [Tenant(*r) for r in rows]

    def issue_key(self, tid: str) -> str:
        self._tenant(tid)
        raw = f"tk_{tid}_{secrets.token_urlsafe(24)}"
        with self.db:
            self.db.execute(
                "INSERT INTO keys(tenant_id, prefix, hash, created_at) VALUES (?,?,?,?)",
                (tid, raw[: len(f"tk_{tid}_") + 8], _hash(raw), _now().isoformat()),
            )
        return raw

    def rotate_key(self, tid: str, grace_hours: int = 24) -> str:
        self._tenant(tid)
        expires = (_now() + timedelta(hours=grace_hours)).isoformat()
        with self.db:
            self.db.execute(
                "UPDATE keys SET expires_at = ? WHERE tenant_id = ?"
                " AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
                (expires, tid, expires),
            )
        return self.issue_key(tid)

    def revoke_key(self, prefix: str) -> None:
        with self.db:
            cur = self.db.execute(
                "UPDATE keys SET revoked_at = ? WHERE prefix = ? AND revoked_at IS NULL",
                (_now().isoformat(), prefix),
            )
        if cur.rowcount == 0:
            raise TtalkkakError(f"키 '{prefix}'를 찾지 못했습니다", 2)

    def active_keys(self, now: datetime | None = None) -> list[KeyRow]:
        now = now or _now()
        rows = self.db.execute(
            "SELECT id, tenant_id, prefix, created_at, expires_at, revoked_at FROM keys"
            " WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            " ORDER BY id",
            (now.isoformat(),),
        ).fetchall()
        return [
            KeyRow(i, t, p, _parse(c), _parse(e), _parse(r))
            for i, t, p, c, e, r in rows
        ]

    def key_hashes(self, now: datetime | None = None) -> dict[str, str]:
        now = now or _now()
        rows = self.db.execute(
            "SELECT hash, tenant_id FROM keys WHERE revoked_at IS NULL"
            " AND (expires_at IS NULL OR expires_at > ?)",
            (now.isoformat(),),
        ).fetchall()
        return dict(rows)

    def resolve(self, raw_key: str, now: datetime | None = None) -> str | None:
        return self.key_hashes(now).get(_hash(raw_key))

    def set_limit(self, tid: str, per_min: int | None) -> None:
        if per_min is not None and per_min <= 0:
            raise TtalkkakError("한도는 1 이상이어야 합니다", 2)
        self._update(tid, "token_limit_per_min = ?", per_min)

    def set_cache(self, tid: str, mode: str) -> None:
        if mode not in CACHE_MODES:
            raise TtalkkakError("캐시 모드는 isolated 또는 shared입니다", 2)
        self._update(tid, "cache_mode = ?", mode)

    def rotate_salt(self, tid: str) -> int:
        self._update(tid, "salt_version = salt_version + ?", 1)
        return self._tenant(tid).salt_version

    def cache_salt(self, tid: str) -> str | None:
        t = self._tenant(tid)
        if t.cache_mode == "shared":
            return None
        msg = f"{t.id}:{t.salt_version}".encode()
        return hmac.new(self._salt_secret.encode(), msg, hashlib.sha256).hexdigest()[
            :32
        ]

    def _update(self, tid: str, assignment: str, value) -> None:
        self._tenant(tid)
        with self.db:
            self.db.execute(
                f"UPDATE tenants SET {assignment} WHERE id = ?", (value, tid)
            )
