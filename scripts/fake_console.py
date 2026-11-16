"""Serve the remote console with canned data (no cluster) for the desktop app.

Usage: .venv/bin/python scripts/fake_console.py WORKDIR [--port 8765]

WORKDIR/console-token is created on first start. WORKDIR/control.json is read on
every request so a scenario can change while the app is open:
  {"capacity": true,        # false -> needs_loadtest
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

CHIP = chip_by_name("nvidia-gpu")
PODS = ["vllm-qwen-7d9f-aaaaa", "vllm-qwen-7d9f-bbbbb"]
MODEL = "Qwen/Qwen3-8B"
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


def build(work: Path) -> UiDeps:
    now0 = datetime.now(UTC)
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
                rec = {
                    "loadtest_id": "20261114-031000",
                    "created_at": (now0 - timedelta(days=2)).isoformat(),
                    "replicas": 2,
                    "model": MODEL,
                    "ttft_target_ms": 1000,
                    "max_tokens_per_min": MAX_TPM,
                }
                data = {
                    "capacity.json": json.dumps({"2": rec})
                    if c.get("capacity", True)
                    else "{}"
                }
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
