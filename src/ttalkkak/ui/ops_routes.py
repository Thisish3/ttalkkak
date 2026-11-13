"""Operator tabs of the local UI: overview, cost, tenants, usage."""

import csv
import io
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ttalkkak.billing.bill import aggregate, invoice_rows, read_log
from ttalkkak.capacity import CapacityRecord
from ttalkkak.cost.model import CostResult
from ttalkkak.errors import TtalkkakError
from ttalkkak.live import prom as live_prom
from ttalkkak.live.prom import PromError
from ttalkkak.live.snapshot import ShortCircuit, build_snapshot

RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")
CONFIG_TTL_S = 10.0


def _err(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


class _NoProm:
    def instant(self, q):
        raise PromError("Prometheus 주소가 설정되지 않았습니다 (--prometheus-url)")

    range = instant


def ops_routes(root: Path, deps, body, bad_request: type[Exception]) -> list[Route]:
    def store():
        if deps.store is None:
            raise TtalkkakError("테넌트 저장소가 설정되지 않았습니다", 503)
        return deps.store

    def tenants_view():
        st = store()
        keys: dict[str, list[dict]] = {}
        for k in st.active_keys():
            keys.setdefault(k.tenant_id, []).append(
                {
                    "prefix": k.prefix,
                    "created_at": k.created_at.isoformat(),
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                }
            )
        return [
            {
                "id": t.id,
                "name": t.name,
                "token_limit_per_min": t.token_limit_per_min,
                "cache_mode": t.cache_mode,
                "salt_version": t.salt_version,
                "keys": keys.get(t.id, []),
            }
            for t in st.list_tenants()
        ]

    def guarded(fn):
        async def handler(request: Request):
            try:
                return await fn(request)
            except TtalkkakError as err:
                return _err(str(err), max(err.exit_code, 400))
            except (bad_request, ValueError) as err:
                return _err(str(err), 400)

        return handler

    def cost_of(run: str | None) -> CostResult | None:
        if not run:
            return None
        if not RUN_ID.match(run):
            raise TtalkkakError("run id가 올바르지 않습니다", 400)
        p = root / "runs" / run / "cost.json"
        if not p.is_file():
            raise TtalkkakError("cost.json이 없습니다: ttalkkak ops cost", 404)
        return CostResult.model_validate_json(p.read_text(encoding="utf-8"))

    def period(request: Request) -> tuple[datetime, datetime]:
        try:
            return tuple(
                datetime.fromisoformat(request.query_params[k]).replace(tzinfo=UTC)
                for k in ("from", "to")
            )
        except (KeyError, ValueError) as err:
            raise TtalkkakError("from/to는 YYYY-MM-DD입니다", 400) from err

    def usage_rows(request: Request) -> list[dict]:
        start, end = period(request)
        cost = cost_of(request.query_params.get("run"))
        lines = deps.gateway_logs() if deps.gateway_logs else []
        usage = aggregate(read_log(lines), start, end)
        if cost is None:
            return [u.model_dump() for u in usage]
        return invoice_rows(usage, cost.price_krw_per_mtok, {})

    @guarded
    async def status(request):
        if deps.cluster_status is None:
            return JSONResponse({"pods": [], "error": "클러스터 연결 없음"})
        try:
            pods = await run_in_threadpool(deps.cluster_status)
        except TtalkkakError as err:
            return JSONResponse({"pods": [], "error": str(err)})
        return JSONResponse({"pods": pods})

    cache: dict[str, tuple[float, object]] = {}

    def configmap(name: str) -> dict | None:
        """data of a ConfigMap in the console namespace, cached 10 s; None if absent."""
        hit = cache.get(name)
        if hit and time.monotonic() - hit[0] < CONFIG_TTL_S:
            return hit[1]
        data = None
        if deps.kubectl is not None:
            try:
                r = deps.kubectl(
                    ["get", "configmap", name, "-n", deps.namespace, "-o", "json"]
                )
                if r.returncode == 0:
                    data = json.loads(r.stdout).get("data") or {}
            except (TtalkkakError, ValueError, OSError):
                data = None
        cache[name] = (time.monotonic(), data)
        return data

    def live_snapshot() -> dict:
        errors: dict[str, str] = {}
        try:
            pods = deps.cluster_status() if deps.cluster_status else []
        except Exception as err:  # noqa: BLE001 - never 500
            pods, errors["replicas"] = [], f"파드 상태를 읽지 못했습니다: {err}"
        prom = ShortCircuit(
            deps.prometheus if deps.prometheus is not None else _NoProm()
        )
        model, running_pods = "", None
        try:
            rows = prom.instant(live_prom.RUNNING)
            running_pods = {m["pod"] for m, _ in rows if m.get("pod")}
            model = next((m["model_name"] for m, _ in rows if m.get("model_name")), "")
        except PromError:
            pass
        auto = configmap("ttalkkak-autoscale")
        deployment = (auto or {}).get("deployment") or ""
        autoscale = {"enabled": None, "max_replicas": None, "decisions": []}
        if auto and deployment and str(auto.get("max_replicas", "")).isdigit():
            vllm_pods = [p for p in pods if p["name"].startswith(deployment)]
            autoscale.update(
                enabled=auto.get("enabled") == "true",
                max_replicas=int(auto["max_replicas"]),
            )
            try:
                autoscale["decisions"] = json.loads(auto.get("decisions") or "[]")
            except ValueError:
                pass
        else:
            vllm_pods = (
                [p for p in pods if p["name"] in running_pods]
                if running_pods is not None
                else []
            )
            autoscale["max_replicas"] = len(vllm_pods)
            errors["autoscale"] = (
                "ttalkkak-autoscale 설정을 읽지 못해 현재 복제본 수를 최대값으로 씁니다"
            )
            if running_pods is None:
                errors["autoscale"] += (
                    ". Prometheus도 응답하지 않아 vLLM 파드를 찾지 못했습니다 (0개로 표시)"
                )
        record = None
        cap = configmap("ttalkkak-capacity")
        try:
            raw = json.loads((cap or {}).get("capacity.json") or "{}")
            record = CapacityRecord.model_validate(raw) if raw else None
        except ValueError:
            errors["capacity_record"] = "용량 기록을 읽지 못했습니다"
        limits = {}
        if deps.store is not None:
            try:
                limits = {
                    t.id: t.token_limit_per_min for t in deps.store.list_tenants()
                }
            except Exception as err:  # noqa: BLE001 - never 500
                errors["tenants"] = f"고객 목록을 읽지 못했습니다: {err}"
        try:
            logs = deps.gateway_logs() if deps.gateway_logs else []
        except Exception:  # noqa: BLE001 - never 500
            logs = []
            errors.setdefault("tenants", "게이트웨이 로그를 읽지 못했습니다")
        chip = deps.chip
        if chip is None:
            from ttalkkak.chips import chip_by_name

            chip = chip_by_name("nvidia-gpu")
        snap = build_snapshot(
            prom, vllm_pods, logs, limits, record, chip, model,
            datetime.now(UTC), autoscale, cost=deps.cost_config,
        )  # fmt: skip
        snap["errors"] = {**errors, **snap["errors"]}
        return snap

    @guarded
    async def live(request):
        return JSONResponse(await run_in_threadpool(live_snapshot))

    @guarded
    async def cost(request):
        c = cost_of(request.query_params.get("run"))
        if c is None:
            raise TtalkkakError("run을 지정하세요", 400)
        return JSONResponse(c.model_dump())

    @guarded
    async def tenants(request):
        if request.method == "POST":
            b = await body(request)
            store().add_tenant(str(b.get("id", "")), str(b.get("name") or b.get("id")))
        return JSONResponse(tenants_view())

    @guarded
    async def issue(request):
        return JSONResponse({"key": store().issue_key(request.path_params["tid"])})

    @guarded
    async def revoke(request):
        store().revoke_key(request.path_params["prefix"])
        return JSONResponse({"ok": True})

    @guarded
    async def setting(request):
        tid, what = request.path_params["tid"], request.path_params["what"]
        st = store()
        if what == "rotate-salt":
            return JSONResponse({"salt_version": st.rotate_salt(tid)})
        b = await body(request)
        if what == "limit":
            v = b.get("per_min")
            st.set_limit(tid, None if v in (None, "") else int(v))
        elif what == "cache":
            st.set_cache(tid, str(b.get("mode")))
        else:
            raise TtalkkakError("unknown setting", 404)
        return JSONResponse({"ok": True})

    @guarded
    async def usage(request):
        return JSONResponse(await run_in_threadpool(usage_rows, request))

    @guarded
    async def invoice(request):
        rows = await run_in_threadpool(usage_rows, request)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["tenant"])
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8")

    return [
        Route("/api/live", live),
        Route("/api/ops/status", status),
        Route("/api/ops/cost", cost),
        Route("/api/ops/tenants", tenants, methods=["GET", "POST"]),
        Route("/api/ops/tenants/{tid}/keys", issue, methods=["POST"]),
        Route("/api/ops/tenants/{tid}/{what}", setting, methods=["POST"]),
        Route("/api/ops/keys/{prefix}/revoke", revoke, methods=["POST"]),
        Route("/api/ops/usage", usage),
        Route("/api/ops/invoice.csv", invoice),
    ]
