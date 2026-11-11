"""OIDC device authorization grant (RFC 8628) for `ttalkkak login`.

The client decodes the ID token only to show who is logged in; the gateway
verifies token signatures.
"""

import base64
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ttalkkak.client.config import ClientConfig, write_private
from ttalkkak.errors import TtalkkakError
from ttalkkak.tenants.store import TENANT_ID

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid profile email groups offline_access"
REFRESH_MARGIN_S = 60
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float
    id_claims: dict


def _claims(id_token: str | None) -> dict:
    if not id_token or id_token.count(".") != 2:
        return {}
    payload = id_token.split(".")[1]
    try:
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return {}


def _check_issuer_url(issuer: str) -> None:
    u = urlparse(issuer)
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOCAL_HOSTS):
        raise TtalkkakError(f"로그인 서버 주소는 https여야 합니다: {issuer}", 2)


class DeviceLogin:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        http: httpx.Client,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ):
        _check_issuer_url(issuer)
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.http = http
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.now = now or (lambda: time.time())
        self._meta: dict | None = None

    def discover(self) -> dict:
        if self._meta is None:
            r = self.http.get(f"{self.issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            meta = r.json()
            if meta.get("issuer", "").rstrip("/") != self.issuer:
                raise TtalkkakError(
                    f"로그인 서버의 issuer가 설정과 다릅니다: {meta.get('issuer')}", 2
                )
            if "device_authorization_endpoint" not in meta:
                raise TtalkkakError(
                    "로그인 서버가 디바이스 로그인을 지원하지 않습니다", 2
                )
            self._meta = meta
        return self._meta

    def start(self) -> dict:
        r = self.http.post(
            self.discover()["device_authorization_endpoint"],
            data={"client_id": self.client_id, "scope": SCOPE},
        )
        r.raise_for_status()
        return r.json()

    def _tokens(self, body: dict, prev: Tokens | None = None) -> Tokens:
        claims = _claims(body.get("id_token")) or (prev.id_claims if prev else {})
        return Tokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token")
            or (prev.refresh_token if prev else None),
            expires_at=self.now() + float(body.get("expires_in", 300)),
            id_claims=claims,
        )

    def poll(self, start: dict) -> Tokens:
        interval = float(start.get("interval", 5))
        deadline = self.now() + float(start.get("expires_in", 600))
        token_url = self.discover()["token_endpoint"]
        while True:
            self.sleep(interval)
            if self.now() >= deadline:
                raise TtalkkakError("로그인 코드가 만료됐습니다. 다시 `ttalkkak login`")
            r = self.http.post(
                token_url,
                data={
                    "grant_type": DEVICE_GRANT,
                    "device_code": start["device_code"],
                    "client_id": self.client_id,
                },
            )
            if r.status_code == 200:
                return self._tokens(r.json())
            error = r.json().get("error") if r.content else None
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise TtalkkakError("브라우저에서 로그인을 거절했습니다")
            if error == "expired_token":
                raise TtalkkakError("로그인 코드가 만료됐습니다. 다시 `ttalkkak login`")
            raise TtalkkakError(f"로그인 실패: {error or r.status_code}")

    def refresh(self, t: Tokens) -> Tokens:
        if not t.refresh_token:
            raise TtalkkakError("로그인이 만료됐습니다: ttalkkak login")
        r = self.http.post(
            self.discover()["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": t.refresh_token,
                "client_id": self.client_id,
            },
        )
        if r.status_code != 200:
            raise TtalkkakError("로그인이 만료됐습니다: ttalkkak login")
        return self._tokens(r.json(), t)

    def revoke(self, t: Tokens) -> None:
        url = self.discover().get("revocation_endpoint")
        token = t.refresh_token or t.access_token
        if url:
            self.http.post(
                url,
                data={
                    "token": token,
                    "token_type_hint": "refresh_token"
                    if t.refresh_token
                    else "access_token",
                    "client_id": self.client_id,
                },
            )


def tenant_of(claims: dict) -> str:
    groups = claims.get("groups")
    if not isinstance(groups, list) or len(groups) != 1:
        raise TtalkkakError(
            "이 계정은 테넌트 하나에 속해야 합니다. 운영자에게 문의하세요", 2
        )
    tid = groups[0]
    if not isinstance(tid, str) or not TENANT_ID.match(tid):
        raise TtalkkakError(f"테넌트 이름이 올바르지 않습니다: {tid}", 2)
    return tid


def _home(home: Path | None) -> Path:
    from ttalkkak.client import config

    return home if home is not None else config.HOME


def save_tokens(t: Tokens, home: Path | None = None) -> Path:
    path = _home(home) / "credentials.json"
    write_private(path, json.dumps(asdict(t)))
    return path


def load_tokens(home: Path | None = None) -> Tokens | None:
    path = _home(home) / "credentials.json"
    if not path.is_file():
        return None
    return Tokens(**json.loads(path.read_text(encoding="utf-8")))


def clear_tokens(home: Path | None = None) -> None:
    (_home(home) / "credentials.json").unlink(missing_ok=True)


def valid_access_token(
    cfg: ClientConfig,
    http: httpx.Client,
    home: Path | None = None,
    now: Callable[[], float] | None = None,
) -> str:
    now = now or time.time
    t = load_tokens(home)
    if t is None or not cfg.issuer:
        raise TtalkkakError("로그인이 필요합니다: ttalkkak login", 2)
    if t.expires_at - now() < REFRESH_MARGIN_S:
        t = DeviceLogin(cfg.issuer, cfg.client_id, http, now=now).refresh(t)
        save_tokens(t, home)
    return t.access_token
