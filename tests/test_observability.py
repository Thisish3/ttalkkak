import json
import re

import yaml

from ttalkkak.cost.model import CostConfig, CostResult, Prices
from ttalkkak.gateway.manifests import GatewayOptions
from ttalkkak.gateway.observability import METRICS, render_observability

# Names checked against vllm/v1/metrics (counters get a _total suffix) and the
# dcgm-exporter defaults; Task 12 re-checks them on a live cluster.
KNOWN = {
    "vllm:num_requests_running", "vllm:num_requests_waiting",
    "vllm:time_to_first_token_seconds_bucket", "vllm:inter_token_latency_seconds_bucket",
    "vllm:generation_tokens_total", "vllm:prompt_tokens_total",
    "vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total",
    "vllm:kv_cache_usage_perc",
    "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_POWER_USAGE", "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_GPU_TEMP",
    "ttalkkak_tokens_total", "ttalkkak_requests_total",
    "ttalkkak_tenant_limit_tokens_per_minute",
}  # fmt: skip
NAME = re.compile(r"(?:vllm:|DCGM_|ttalkkak_)[A-Za-z0-9_:]+")
COST = CostResult(
    e_in_j=1, e_cached_j=0.1, e_out_j=5, warnings=[], operating_point=None,
    gpus_needed=None, cost_krw_per_mtok=Prices(input=1, cached_input=1, output=1),
    price_krw_per_mtok=Prices(input=100, cached_input=10, output=500),
    config=CostConfig(electricity_krw_per_kwh=150, pue=1.4),
)  # fmt: skip


def exprs(files):
    out = []
    for name in ("grafana/gpu.json", "grafana/serving.json", "grafana/usage.json"):
        d = json.loads(files[name])
        out += [t["expr"] for p in d["panels"] for t in p.get("targets", [])]
    rules = yaml.safe_load(files["alerts.yaml"])
    out += [r["expr"] for g in rules["groups"] for r in g["rules"]]
    return out


def test_dashboards_and_alerts_only_use_known_metrics():
    used = {
        m
        for e in exprs(render_observability(None, GatewayOptions()))
        for m in NAME.findall(e)
    }
    assert used and used <= KNOWN, used - KNOWN
    assert METRICS <= KNOWN


def test_usage_dashboard_uses_cost_prices():
    text = render_observability(COST, GatewayOptions())["grafana/usage.json"]
    assert "500" in text and "150" in text and "1.4" in text


def test_alerts_cover_slo_kv_quota_temp_down():
    rules = yaml.safe_load(render_observability(None, GatewayOptions())["alerts.yaml"])
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    assert {
        "TtftSloBreach",
        "ItlSloBreach",
        "KvCacheHigh",
        "TenantQuota80",
        "GpuHot",
        "EngineDown",
    } <= names
    assert all(r["for"] == "5m" for g in rules["groups"] for r in g["rules"])


def test_slo_thresholds_follow_cost_config():
    rules = render_observability(COST, GatewayOptions())["alerts.yaml"]
    assert "> 1.0" in rules and "> 0.1" in rules  # 1000 ms TTFT, 100 ms ITL


def test_stack_has_prometheus_grafana_otel_jaeger_dcgm():
    docs = list(
        yaml.safe_load_all(
            render_observability(None, GatewayOptions())["50-observability.yaml"]
        )
    )
    names = {
        d["metadata"]["name"] for d in docs if d["kind"] in ("Deployment", "DaemonSet")
    }
    assert {
        "prometheus",
        "grafana",
        "otel-collector",
        "jaeger",
        "dcgm-exporter",
    } <= names
    graf = next(
        d
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "grafana"
    )
    env = {
        e["name"]: e for e in graf["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["GF_AUTH_ANONYMOUS_ENABLED"]["value"] == "false"
    assert "secretKeyRef" in env["GF_SECURITY_ADMIN_PASSWORD"]["valueFrom"]
