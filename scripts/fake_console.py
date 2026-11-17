"""Serve the remote console with canned data (no cluster) for the desktop app.

Usage: .venv/bin/python scripts/fake_console.py WORKDIR [--port 8765]

WORKDIR/console-token is created on first start. WORKDIR/control.json is read on
every request so a scenario can change while the app is open:
  {"capacity": true,        # false -> needs_loadtest, "unconfirmed" -> 확인 실패
   "recent_429": 0,         # gateway log rows with status 429 in the last seconds
   "decisions": []}         # extra AUTOSCALE log rows: {"t","action","from_replicas",...}
"""

import argparse
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

import httpx
import uvicorn

from ttalkkak.chips import chip_by_name
from ttalkkak.cost.model import CostConfig, CostResult, Prices
from ttalkkak.live import prom as P
from ttalkkak.live.prom import Prom
from ttalkkak.tenants.store import TenantStore
from ttalkkak.ui import auth
from ttalkkak.ui.app import UiDeps, allowed_hosts, create_app

REAL_CHIP = chip_by_name("nvidia-gpu")
CHIP = REAL_CHIP.model_copy(
    update={"name": f"nvidia-gpu · {'가짜 데이터 (fake_console)'}"}
)
PODS = ["vllm-qwen-fake-aaaaa", "vllm-qwen-fake-bbbbb"]
FAKE = "가짜 데이터 (fake_console)"
MODEL = f"FAKE-Qwen3-8B · {FAKE}"
MAX_TPM = 21500


def control(work: Path) -> dict:
    try:
        return json.loads((work / "control.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def vec(rows):
    now = time.time()
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": m, "value": [now, str(v)]} for m, v in rows],
        },
    }


def matrix(rows):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": m, "values": v} for m, v in rows],
        },
    }


def prom_client() -> Prom:
    power = CHIP.telemetry.metrics["power"]
    util = CHIP.telemetry.metrics["utilization"]
    pod = lambda v: [({"pod": p, "model_name": MODEL}, x) for p, x in zip(PODS, v)]
    instant = {
        P.TPM: [({}, 9200)],
        P.RUNNING: pod((3, 2)),
        P.WAITING: pod((1, 0)),
        P.KV: pod((0.41, 0.28)),
        P.PROMPT: pod((900, 700)),
        P.CACHED: pod((560, 300)),
        P.TTFT_P99: [({}, 0.412)],
        P.ITL_P99: [({}, 0.021)],
        P.OUTPUT_TPS: [({}, 830)],
        f"sum({power})": [({}, 612)],
        util: pod((71, 54)),
    }

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        if req.url.path.endswith("query_range"):
            now = time.time()
            if q == P.TPM:
                pts = [
                    [
                        now - 3600 + i * 30,
                        str(
                            MAX_TPM
                            * (
                                0.35
                                + 0.3 * math.sin(i / 14) ** 2
                                + (0.25 if 70 < i < 80 else 0)
                            )
                        ),
                    ]
                    for i in range(121)
                ]
                return httpx.Response(200, json=matrix([({}, pts)]))
            if q == P.RUNNING:
                pts = [[now - 300 + i * 30, "2"] for i in range(11)]
                return httpx.Response(
                    200, json=matrix([({"pod": p}, pts) for p in PODS])
                )
            return httpx.Response(200, json=matrix([]))
        return httpx.Response(200, json=vec(instant.get(q, [])))

    return Prom(
        "http://prom:9090", httpx.Client(transport=httpx.MockTransport(handler))
    )


def capacity_json(state, now: datetime) -> str:
    if not state:
        return "{}"
    rec = {
        "loadtest_id": "20261114-031000",
        "created_at": (now - timedelta(days=2)).isoformat(),
        "replicas": 2,
        "model": MODEL,
        "ttft_target_ms": 1000,
        "max_tokens_per_min": MAX_TPM,
        "method": "goodput",
        "target_attainment": 0.95,
        "stop_reason": "converged",
        "capacity_streams": 12,
        "confirmed": state != "unconfirmed",
        "confirm_rate_rps": 2.4,
        "knee_streams": 10.5,
        "knee_output_tok_s": 820.0,
    }
    return json.dumps({"2": rec})


def _probe(users, attainment, lower, upper, passed, resolved):
    return {
        "users": users, "ttft_p99_ms": 90.0 * users, "itl_p99_ms": 21.0,
        "output_tok_s": min(70.0 * users, 840.0), "tokens_per_min": min(1800.0 * users, MAX_TPM),
        "error_rate": 0.0, "request_rate": min(0.22 * users, 2.7),
        "attainment": attainment, "attainment_lower": lower, "attainment_upper": upper,
        "determined_requests": 150, "passed": passed, "resolved": resolved, "aborted": False,
    }  # fmt: skip


def seed_loadtests(work: Path, now: datetime) -> None:
    """Three finished goodput runs: confirmed, open-loop miss, indeterminate."""
    d = work / "runs" / "loadtests"
    d.mkdir(parents=True, exist_ok=True)
    probes = [
        _probe(1, 1.0, 0.976, 1.0, True, True),
        _probe(2, 1.0, 0.976, 1.0, True, True),
        _probe(4, 0.993, 0.963, 0.999, True, True),
        _probe(8, 0.987, 0.953, 0.996, True, True),
        _probe(16, 0.81, 0.74, 0.86, False, True),
        _probe(12, 0.973, 0.934, 0.99, True, False),
        _probe(10, 0.993, 0.963, 0.999, True, True),
        _probe(11, 0.94, 0.89, 0.97, False, False),
    ]
    confirm = {
        "rate_rps": 1.98, "achieved_rps": 1.97, "attainment": 0.986,
        "attainment_lower": 0.971, "attainment_upper": 0.993, "determined_requests": 118,
        "error_rate": 0.0, "tokens_per_min": 17500.0, "ttft_p99_ms": 880.0,
        "held": True, "resolved": True,
    }  # fmt: skip
    base = {
        "model": MODEL, "autoscale_on": False, "method": "goodput", "max_users": 20,
        "ttft_target_ms": 1000, "itl_target_ms": None, "target_attainment": 0.95,
        "phase": "done", "stages": probes, "stop_reason": "converged",
        "best_passing_streams": 12, "lowest_failing_streams": 11,
        "capacity_streams": 10, "capacity_tpm": 18000.0, "no_capacity_reason": None,
        "confirm": confirm, "error": None,
        "knee": {"status": "ok", "streams": 10.5, "output_tok_s": 770.0, "points": 8,
                 "reason": None},
    }  # fmt: skip
    runs = [
        {**base, "id": "20261114-031000", "replicas": 2},
        {
            **base, "id": "20261115-090000", "replicas": 1,
            "confirm": {**confirm, "attainment": 0.79, "attainment_lower": 0.71,
                        "attainment_upper": 0.85, "held": False},
        },
        {
            **base, "id": "20261116-100000", "replicas": 3, "stages": probes[:1],
            "stop_reason": "indeterminate_at_minimum", "best_passing_streams": None,
            "lowest_failing_streams": 1, "capacity_streams": None, "capacity_tpm": None,
            "confirm": None,
            "knee": {"status": "no_knee", "streams": None, "output_tok_s": None,
                     "points": 1, "reason": "동시 사용자 단계가 1개라 무릎을 계산하지 않았습니다 (최소 5개)"},
            "no_capacity_reason": "요청 수가 적어 가장 낮은 단계에서도 목표 달성 여부를 판정할 수 없습니다. 단계 시간을 늘려 다시 시험하세요",
        },
    ]  # fmt: skip
    for i, run in enumerate(runs):
        path = d / f"{run['id']}.json"
        if not path.exists():
            created = now - timedelta(days=len(runs) - i)
            path.write_text(
                json.dumps({**run, "created_at": created.isoformat()}, ensure_ascii=False),
                encoding="utf-8",
            )


def build(work: Path) -> UiDeps:
    now0 = datetime.now(UTC)
    seed_loadtests(work, now0)
    store = TenantStore(work / "tenants.db")
    for tid, name, limit in (("acme", "Acme Corp", 6000), ("free", "무료 체험", None)):
        if tid not in {t.id for t in store.list_tenants()}:
            store.add_tenant(tid, name)
        store.set_limit(tid, limit)

    def kubectl(args, input=None):
        c = control(work)
        if args[:2] == ["get", "configmap"]:
            if args[2] == "ttalkkak-autoscale":
                data = {
                    "enabled": "true",
                    "max_replicas": "3",
                    "deployment": "vllm-qwen",
                }
            else:
                data = {"capacity.json": capacity_json(c.get("capacity", True), now0)}
            return CompletedProcess(args, 0, json.dumps({"data": data}), "")
        if args[:1] == ["logs"]:
            lines = [f"AUTOSCALE {json.dumps(d)}" for d in c.get("decisions", [])]
            return CompletedProcess(args, 0, "\n".join(lines) + "\n", "")
        return CompletedProcess(args, 1, "", "not available in the fake console")

    def gateway_logs() -> list[str]:
        now = datetime.now(UTC)
        row = lambda tenant, status, ago, tok: json.dumps(
            {
                "ts": (now - timedelta(seconds=ago)).isoformat(),
                "tenant": tenant,
                "model": MODEL,
                "status": status,
                "input_tokens": tok,
                "output_tokens": 0,
            }
        )
        out = [row("acme", 200, 20, 5800), row("free", 200, 15, 10)]
        out += [row("acme", 429, 600 + i * 30, 0) for i in range(14)]
        out += [
            row("acme", 429, 3 + i % 5, 0)
            for i in range(int(control(work).get("recent_429", 0)))
        ]
        return out

    price = Prices(input=1_000_000, cached_input=100_000, output=2_000_000)
    (work / "runs" / "r1").mkdir(parents=True, exist_ok=True)
    cost = CostResult(
        e_in_j=1, e_cached_j=0.1, e_out_j=5, warnings=["샘플 데이터"], operating_point=None,
        gpus_needed=None, cost_krw_per_mtok=price, price_krw_per_mtok=price, config=CostConfig(),
    )  # fmt: skip
    (work / "runs" / "r1" / "cost.json").write_text(cost.model_dump_json())
    pods = [
        {"name": p, "ready": "1/1", "restarts": 0, "phase": "Running"} for p in PODS
    ]
    pods.append(
        {"name": "gateway-5c8d-xyz", "ready": "1/1", "restarts": 1, "phase": "Running"}
    )
    return UiDeps(
        list_models=list, detect_gpu=lambda: None, run_fn=lambda *a: None, store=store,
        cluster_status=lambda: pods, gateway_logs=gateway_logs, prometheus=prom_client(),
        kubectl=kubectl, chip=CHIP, cost_config=CostConfig(),
    )  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--port", type=int, default=18765)
    a = ap.parse_args()
    work = Path(a.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    token_path = work / "console-token"
    if auth.load_token(token_path) is None:
        auth.save_token(token_path, auth.new_token())
    app = create_app(work, build(work), allowed_hosts(), token_path)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
