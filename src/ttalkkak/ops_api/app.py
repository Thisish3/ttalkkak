"""`ttalkkak ops serve`: gateway auth (/authz), pricing, usage and per-tenant metrics."""

import hashlib
import hmac
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ttalkkak.billing.bill import (
    REJECTED,
    aggregate,
    billable,
    invoice_rows,
    read_log,
)
from ttalkkak.cost.model import CostResult
from ttalkkak.errors import TtalkkakError
from ttalkkak.tenants.store import TENANT_ID

UNAUTHORIZED = {"www-authenticate": 'Bearer realm="ttalkkak"'}
ALGORITHMS = ["RS256", "ES256"]


@dataclass
class AuthConfig:
    issuer: str
    client_id: str
    jwks: Callable[[], dict]


def _verify_jwt(raw: str, auth: AuthConfig) -> dict | None:
    try:
        kid = jwt.get_unverified_header(raw).get("kid")
        keys = jwt.PyJWKSet.from_dict(auth.jwks()).keys
        key = next((k for k in keys if k.key_id == kid), None)
        if key is None:
            return None
        return jwt.decode(
            raw,
            key.key,
            algorithms=ALGORITHMS,
            audience=auth.client_id,
            issuer=auth.issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def _single_tenant(claims: dict) -> str | None:
    groups = claims.get("groups")
    if (
        isinstance(groups, list)
        and len(groups) == 1
        and TENANT_ID.match(str(groups[0]))
    ):
        return groups[0]
    return None


def _lookup_key(raw: str, keys: dict[str, str]) -> str | None:
    digest = hashlib.sha256(raw.encode()).hexdigest()
    for h, tenant in keys.items():
        if hmac.compare_digest(h, digest):
            return tenant
    return None


def _tenant_header(request: Request) -> str:
    t = request.headers.get("x-ttalkkak-tenant", "")
    if not TENANT_ID.match(t):
        raise TtalkkakError("x-ttalkkak-tenant 헤더가 없거나 올바르지 않습니다", 400)
    return t


def _prom_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def create_ops_api(
    tenants: Callable[[], dict],
    cost: CostResult | None,
    log_source: Callable[[], Iterable[str]],
    auth: AuthConfig,
) -> Starlette:
    async def authz(request: Request) -> Response:
        header = request.headers.get("authorization", "")
        raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not raw:
            return Response(status_code=401, headers=UNAUTHORIZED)
        data = tenants()
        if raw.startswith("tk_"):
            tenant, subject = _lookup_key(raw, data.get("keys", {})), "api-key"
            if tenant is None:
                return Response(status_code=401, headers=UNAUTHORIZED)
        else:
            claims = _verify_jwt(raw, auth)
            tenant = _single_tenant(claims) if claims else None
            if tenant is None:
                return Response(status_code=401, headers=UNAUTHORIZED)
            subject = str(claims["sub"])
            if tenant not in data.get("tenants", []):
                return Response(status_code=403)
        headers = {"x-ttalkkak-tenant": tenant, "x-ttalkkak-subject": subject}
        # always set so a client-sent salt header never reaches the Lua filter
        headers["x-ttalkkak-cache-salt"] = data.get("salts", {}).get(tenant) or ""
        return Response(status_code=200, headers=headers)

    async def pricing(request: Request) -> Response:
        if cost is None:
            return JSONResponse({"error": "cost.json이 없습니다"}, status_code=503)
        p = cost.price_krw_per_mtok
        return JSONResponse(
            {"currency": "KRW", "per": 1_000_000, **p.model_dump(), "note": "estimate"}
        )

    async def auth_config(request: Request) -> Response:
        return JSONResponse({"issuer": auth.issuer, "client_id": auth.client_id})

    def _window(tenant: str, start: datetime, end: datetime) -> dict:
        usage = [
            u
            for u in aggregate(read_log(log_source()), start, end)
            if u.tenant == tenant
        ]
        totals = {
            k: sum(getattr(u, k) for u in usage)
            for k in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "requests",
            )
        }
        amount = None
        if cost is not None:
            amount = sum(
                r["amount_krw"]
                for r in invoice_rows(usage, cost.price_krw_per_mtok, {})
            )
        return {**totals, "amount_krw": amount}

    async def usage(request: Request) -> Response:
        try:
            tenant = _tenant_header(request)
        except TtalkkakError as err:
            return JSONResponse({"error": str(err)}, status_code=400)
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        return JSONResponse(
            {
                "tenant": tenant,
                "today": _window(tenant, day, now),
                "month": _window(tenant, month, now),
                "note": "estimate",
            }
        )

    async def metrics(request: Request) -> Response:
        records = list(read_log(log_source()))
        lo = datetime.min.replace(tzinfo=UTC)
        hi = datetime.max.replace(tzinfo=UTC)
        tokens: dict[tuple, int] = {}
        for r in billable(records, lo, hi):
            key = (r["tenant"], r["model"])
            for kind, v in (
                ("input", r["input_tokens"]),
                ("cached_input", r["cached_input_tokens"]),
                ("output", r["output_tokens"]),
                ("total", r["input_tokens"] + r["output_tokens"]),
            ):
                tokens[(*key, kind)] = tokens.get((*key, kind), 0) + v
        reqs: dict[tuple, int] = {}
        seen = set()
        for r in records:
            if r["status"] in REJECTED or not TENANT_ID.match(r["tenant"]):
                continue
            if r["request_id"]:
                if (r["tenant"], r["request_id"]) in seen:
                    continue
                seen.add((r["tenant"], r["request_id"]))
            k = (r["tenant"], str(r["status"]))
            reqs[k] = reqs.get(k, 0) + 1
        lines = ["# TYPE ttalkkak_tokens_total counter"]
        lines += [
            f'ttalkkak_tokens_total{{tenant="{_prom_label(t)}",model="{_prom_label(m)}",'
            f'kind="{k}"}} {v}'
            for (t, m, k), v in sorted(tokens.items())
        ]
        lines.append("# TYPE ttalkkak_requests_total counter")
        lines += [
            f'ttalkkak_requests_total{{tenant="{_prom_label(t)}",status="{s}"}} {v}'
            for (t, s), v in sorted(reqs.items())
        ]
        lines.append("# TYPE ttalkkak_tenant_limit_tokens_per_minute gauge")
        lines += [
            f'ttalkkak_tenant_limit_tokens_per_minute{{tenant="{_prom_label(t)}"}} {v}'
            for t, v in sorted(tenants().get("limits", {}).items())
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    return Starlette(
        routes=[
            Route(
                "/authz{rest:path}",
                authz,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            ),
            Route("/v1/pricing", pricing),
            Route("/v1/auth-config", auth_config),
            Route("/v1/usage", usage),
            Route("/metrics", metrics),
        ]
    )


def build_serve_app(
    tenants_path,
    cost_path,
    logs,
    issuer: str,
    client_id: str,
    jwks_url: str,
    logs_from_cluster: bool = False,
) -> Starlette:
    import httpx

    from ttalkkak.ops_api import sources

    cost = None
    if cost_path is not None and cost_path.is_file():
        cost = CostResult.model_validate_json(cost_path.read_text(encoding="utf-8"))
    if logs_from_cluster:
        log_source = sources.cluster_logs()
    elif logs is not None:
        log_source = sources.log_files(logs)
    else:
        log_source = list
    return create_ops_api(
        tenants=sources.json_file(tenants_path),
        cost=cost,
        log_source=log_source,
        auth=AuthConfig(
            issuer=issuer,
            client_id=client_id,
            jwks=sources.jwks_url(jwks_url, httpx.Client(timeout=10)),
        ),
    )
