import base64
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

ISSUER = "https://idp.test"


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


@dataclass
class FakeOidc:
    pending_polls: int = 0
    deny: bool = False
    expire: bool = False
    slow_down_once: bool = False
    groups: list[str] = field(default_factory=lambda: ["acme"])
    issuer: str = ISSUER
    revoked: list[str] = field(default_factory=list)
    issued: int = 0

    def _token(self) -> httpx.Response:
        self.issued += 1
        claims = {"sub": "u1", "email": "u1@acme", "groups": self.groups}
        return httpx.Response(
            200,
            json={
                "access_token": f"at-{self.issued}",
                "refresh_token": f"rt-{self.issued}",
                "expires_in": 900,
                "id_token": f"{_b64({'alg': 'none'})}.{_b64(claims)}.sig",
                "token_type": "Bearer",
            },
        )

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "device_authorization_endpoint": f"{ISSUER}/device/code",
                    "token_endpoint": f"{ISSUER}/token",
                    "revocation_endpoint": f"{ISSUER}/revoke",
                },
            )
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if path == "/device/code":
            return httpx.Response(
                200,
                json={
                    "device_code": "dc",
                    "user_code": "ABCD-1234",
                    "verification_uri": f"{ISSUER}/device",
                    "verification_uri_complete": f"{ISSUER}/device?user_code=ABCD-1234",
                    "interval": 5,
                    "expires_in": 600,
                },
            )
        if path == "/token" and form.get("grant_type", "").endswith("device_code"):
            if self.deny:
                return httpx.Response(400, json={"error": "access_denied"})
            if self.expire:
                return httpx.Response(400, json={"error": "expired_token"})
            if self.slow_down_once:
                self.slow_down_once = False
                return httpx.Response(400, json={"error": "slow_down"})
            if self.pending_polls > 0:
                self.pending_polls -= 1
                return httpx.Response(400, json={"error": "authorization_pending"})
            return self._token()
        if path == "/token" and form.get("grant_type") == "refresh_token":
            return self._token()
        if path == "/revoke":
            self.revoked.append(form["token"])
            return httpx.Response(200)
        return httpx.Response(404)


def make_oidc(**state) -> tuple[httpx.Client, FakeOidc]:
    srv = FakeOidc(**state)
    return httpx.Client(transport=httpx.MockTransport(srv.handler)), srv
