"""Operator tabs of the local UI: overview, cost, tenants, usage."""

import csv
import importlib.metadata
import io
import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ttalkkak.billing.bill import aggregate, invoice_rows, read_log
from ttalkkak.capacity import (
    missing_loadtest_counts,
    missing_loadtest_text,
    parse_capacity,
    record_for,
)
from ttalkkak.cost.model import CostResult
from ttalkkak.errors import TtalkkakError
from ttalkkak.live import prom as live_prom
from ttalkkak.live.loadtest import (
    PROBE_SECONDS,
    LoadTestRunner,
    check_max_users,
    check_probe_seconds,
)
from ttalkkak.live.prom import PromError
from ttalkkak.live.snapshot import ShortCircuit, build_snapshot
from ttalkkak.ops_api.autoscaler import parse_decisions

RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")
POD_NAME = re.compile(r"^[a-z0-9-]+$")


def is_replica(name: str, deployment: str) -> bool:
    """vLLM Deployment의 파드인지. 같은 접두어를 쓰는 EPP 파드(<deployment>-epp-...)는 뺀다."""
    return bool(deployment) and (
        name.startswith(f"{deployment}-") and not name.startswith(f"{deployment}-epp-")
    )


CONFIG_TTL_S = 10.0
RECENT_DECISION_FILES = 2
OPS_DEPLOY = "ttalkkak-ops"


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


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

    decisions_dir = root / "runs" / "autoscale"

    sync_lock = threading.Lock()
    synced_at = [float("-inf")]

    def saved_decisions(days: int = RECENT_DECISION_FILES) -> list[dict]:
        """Rows of the newest `days` files, newest first; bad files/rows skipped."""
        rows: list[dict] = []
        for f in sorted(decisions_dir.glob("*.jsonl"))[-days:]:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                        _ts(row["t"])
                        row["action"]
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                    rows.append(row)
            except OSError:
                continue
        rows.sort(key=lambda d: _ts(d["t"]), reverse=True)
        return rows

    def sync_decisions() -> list[dict]:
        """Append the ops pod's new AUTOSCALE log lines to runs/autoscale/<date>.jsonl."""
        if deps.kubectl is None:
            raise TtalkkakError("클러스터 연결 없음", 503)
        with sync_lock:
            r = deps.kubectl(
                ["logs", f"deploy/{OPS_DEPLOY}", "-n", deps.namespace, "--since=24h"]
            )
            if r.returncode != 0:
                lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
                why = lines[-1].strip() if lines else f"exit {r.returncode}"
                raise TtalkkakError(f"자동 증감 기록을 읽지 못했습니다: {why}", 502)
            known = saved_decisions()
            seen = {(_ts(d["t"]), d["action"]) for d in known}
            by_file: dict[str, list[dict]] = {}
            for d in parse_decisions((r.stdout or "").splitlines()):
                key = (_ts(d["t"]), d["action"])
                if key not in seen:
                    seen.add(key)
                    by_file.setdefault(key[0].strftime("%Y-%m-%d"), []).append(d)
            if by_file:
                decisions_dir.mkdir(parents=True, exist_ok=True)
            for day, items in by_file.items():
                with (decisions_dir / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(_jsonl(items))
            synced_at[0] = time.monotonic()
            return saved_decisions()

    def recent_decisions() -> list[dict]:
        """Decisions for the snapshot; syncs from the log at most every TTL."""
        if deps.kubectl is not None and (
            time.monotonic() - synced_at[0] >= CONFIG_TTL_S
        ):
            try:
                return sync_decisions()[:10]
            except (TtalkkakError, OSError, ValueError):
                synced_at[0] = time.monotonic()
        return saved_decisions()[:10]

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
        autoscale = {
            "enabled": None,
            "max_replicas": None,
            "decisions": recent_decisions(),
        }
        if auto and deployment and str(auto.get("max_replicas", "")).isdigit():
            vllm_pods = [p for p in pods if is_replica(p["name"], deployment)]
            autoscale.update(
                enabled=auto.get("enabled") == "true",
                max_replicas=int(auto["max_replicas"]),
            )
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
        record, records = None, {}
        cap = configmap("ttalkkak-capacity")
        try:
            records = parse_capacity((cap or {}).get("capacity.json"), deps.namespace)
            record = record_for(records, len(vllm_pods))
        except (TtalkkakError, ValueError):
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
            missing_loadtest=missing_loadtest_counts(records, autoscale["max_replicas"] or 0),
            missing_text=missing_loadtest_text(records, autoscale["max_replicas"] or 0),
        )  # fmt: skip
        snap["errors"] = {**errors, **snap["errors"]}
        return snap

    def current_replicas() -> int | None:
        """vLLM pods Prometheus sees running among the cluster's pods (as /api/live)."""
        try:
            pods = deps.cluster_status() if deps.cluster_status else []
            prom = deps.prometheus if deps.prometheus is not None else _NoProm()
            rows = prom.instant(live_prom.RUNNING)
        except Exception:  # noqa: BLE001 - ping must answer
            return None
        running = {m["pod"] for m, _ in rows if m.get("pod")}
        return len([p for p in pods if p["name"] in running])

    def package_version() -> str:
        try:
            return importlib.metadata.version("ttalkkak")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    @guarded
    async def ping(request):
        auto = await run_in_threadpool(configmap, "ttalkkak-autoscale") or {}
        limit = str(auto.get("max_replicas", ""))
        body = {
            "product": "ttalkkak",
            "version": package_version(),
            "chip": deps.chip.name if deps.chip is not None else None,
            "max_replicas": int(limit) if limit.isdigit() else None,
        }
        if body["max_replicas"] is None:
            body["max_replicas"] = await run_in_threadpool(current_replicas)
            if body["max_replicas"] is not None:
                body["error"] = (
                    "ttalkkak-autoscale 설정을 읽지 못해 현재 복제본 수를 쓰고 있습니다"
                )
        return JSONResponse(body)

    @guarded
    async def live(request):
        return JSONResponse(await run_in_threadpool(live_snapshot))

    def autoscale_config() -> tuple[str, int]:
        auto = configmap("ttalkkak-autoscale")
        deployment = (auto or {}).get("deployment") or ""
        limit = str((auto or {}).get("max_replicas", ""))
        if not deployment or not limit.isdigit():
            raise TtalkkakError(
                "ttalkkak-autoscale 설정을 읽지 못했습니다 (배포 이름과 최대 복제본 수)",
                503,
            )
        return deployment, int(limit)

    def kube(args: list[str]) -> None:
        if deps.kubectl is None:
            raise TtalkkakError("클러스터 연결 없음", 503)
        r = deps.kubectl(args)
        if r.returncode != 0:
            lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
            why = lines[-1].strip() if lines else f"exit {r.returncode}"
            raise TtalkkakError(f"kubectl이 실패했습니다: {why}", 502)

    @guarded
    async def scale(request):
        b = await body(request)
        count = b.get("count")
        deployment, limit = await run_in_threadpool(autoscale_config)
        if isinstance(count, bool) or not isinstance(count, int):
            raise TtalkkakError("count는 정수입니다", 400)
        if not 1 <= count <= limit:
            raise TtalkkakError(f"복제본 수는 1~{limit}이어야 합니다", 400)
        await run_in_threadpool(
            kube,
            ["scale", f"deployment/{deployment}", f"--replicas={count}",
             "-n", deps.namespace],
        )  # fmt: skip
        cache.pop("ttalkkak-autoscale", None)
        return JSONResponse({"ok": True, "count": count})

    @guarded
    async def autoscale_toggle(request):
        b = await body(request)
        enabled = b.get("enabled")
        if not isinstance(enabled, bool):
            raise TtalkkakError("enabled는 불리언입니다", 400)
        value = json.dumps({"data": {"enabled": "true" if enabled else "false"}})
        await run_in_threadpool(
            kube,
            ["patch", "configmap", "ttalkkak-autoscale", "-n", deps.namespace,
             "--type", "merge", "-p", value],
        )  # fmt: skip
        cache.pop("ttalkkak-autoscale", None)
        return JSONResponse({"ok": True, "enabled": enabled})

    @guarded
    async def autoscale_decisions(request):
        return JSONResponse(await run_in_threadpool(sync_decisions))

    @guarded
    async def delete_pod(request):
        pod = request.path_params["pod"]
        deployment, _ = await run_in_threadpool(autoscale_config)
        pods = (
            await run_in_threadpool(deps.cluster_status) if deps.cluster_status else []
        )
        if not POD_NAME.fullmatch(pod) or pod not in {
            p["name"] for p in pods if is_replica(p["name"], deployment)
        }:
            raise TtalkkakError("vLLM 파드가 아닙니다", 400)
        await run_in_threadpool(kube, ["delete", "pod", pod, "-n", deps.namespace])
        return JSONResponse({"ok": True})

    runner_box: list[LoadTestRunner] = []
    runner_lock = threading.Lock()

    def loadtests(start: bool = False) -> LoadTestRunner:
        if start and not deps.gateway_url:
            raise TtalkkakError(
                "게이트웨이 주소가 설정되지 않았습니다 (--gateway-url)", 503
            )
        with runner_lock:
            if not runner_box:
                runner_box.append(
                    LoadTestRunner(
                        root, deps.store, deps.gateway_url, "",
                        kubectl=deps.kubectl, namespace=deps.namespace,
                    )
                )  # fmt: skip
            return runner_box[0]

    def start_loadtest(b: dict) -> str:
        store()
        runner = loadtests(start=True)
        users, on = b.get("max_users"), b.get("autoscale_on", False)
        if not isinstance(users, int) or not isinstance(on, bool):
            raise TtalkkakError("max_users는 정수, autoscale_on은 불리언입니다", 400)
        check_max_users(users)
        probe_seconds = b.get("probe_seconds", PROBE_SECONDS)
        try:
            check_probe_seconds(probe_seconds)
        except ValueError as err:
            raise TtalkkakError(str(err), 400) from err
        if runner.busy:
            raise TtalkkakError("이미 부하 시험이 실행 중입니다", 409)
        deployment, _ = autoscale_config()
        pods = deps.cluster_status() if deps.cluster_status else []
        replicas = sum(
            1 for p in pods
            if is_replica(p["name"], deployment) and p["phase"] == "Running"
        )  # fmt: skip
        model = ""
        try:
            rows = ShortCircuit(deps.prometheus or _NoProm()).instant(live_prom.RUNNING)
            model = next((m["model_name"] for m, _ in rows if m.get("model_name")), "")
        except PromError:
            pass
        if not model or replicas < 1:
            raise TtalkkakError("실행 중인 vLLM 모델을 찾지 못했습니다", 503)
        return runner.start(users, replicas, on, model, probe_seconds=probe_seconds)

    @guarded
    async def loadtest_start(request):
        b = await body(request)
        return JSONResponse({"id": await run_in_threadpool(start_loadtest, b)})

    @guarded
    async def loadtest_status(request):
        return JSONResponse(loadtests().status(request.path_params["tid"]))

    @guarded
    async def loadtest_list(request):
        return JSONResponse(await run_in_threadpool(loadtests().list))

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
        Route("/api/ping", ping),
        Route("/api/live", live),
        Route("/api/loadtest", loadtest_start, methods=["POST"]),
        Route("/api/loadtest/{tid}", loadtest_status),
        Route("/api/loadtests", loadtest_list),
        Route("/api/replicas", scale, methods=["POST"]),
        Route("/api/autoscale", autoscale_toggle, methods=["POST"]),
        Route("/api/autoscale/decisions", autoscale_decisions),
        Route("/api/replicas/{pod}/delete", delete_pod, methods=["POST"]),
        Route("/api/ops/status", status),
        Route("/api/ops/cost", cost),
        Route("/api/ops/tenants", tenants, methods=["GET", "POST"]),
        Route("/api/ops/tenants/{tid}/keys", issue, methods=["POST"]),
        Route("/api/ops/tenants/{tid}/{what}", setting, methods=["POST"]),
        Route("/api/ops/keys/{prefix}/revoke", revoke, methods=["POST"]),
        Route("/api/ops/usage", usage),
        Route("/api/ops/invoice.csv", invoice),
    ]
