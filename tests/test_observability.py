import json
import re
from pathlib import Path

import yaml

from ttalkkak.chips import chip_by_name
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


NOT_PROVIDED = "이 칩은 이 지표를 내지 않음"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/observability_nvidia.json").read_text()
)


def test_nvidia_dashboards_unchanged():
    nv = chip_by_name("nvidia-gpu")
    assert render_observability(COST, GatewayOptions(), nv) == FIXTURE["with_cost"]
    assert render_observability(None, GatewayOptions(), nv) == FIXTURE["no_cost"]
    assert render_observability(None, GatewayOptions()) == FIXTURE["no_cost"]


def test_amd_no_exporter_and_panels_say_not_provided():
    files = render_observability(COST, GatewayOptions(), chip_by_name("amd-gpu"))
    docs = yaml.safe_load_all(files["50-observability.yaml"])
    assert "dcgm-exporter" not in {d["metadata"]["name"] for d in docs}
    gpu = json.loads(files["grafana/gpu.json"])
    assert len(gpu["panels"]) == 4
    assert all(
        p["type"] == "text" and NOT_PROVIDED in p["title"] for p in gpu["panels"]
    )
    usage = json.loads(files["grafana/usage.json"])
    power = next(p for p in usage["panels"] if p["title"].startswith("전기료"))
    assert power["type"] == "text" and NOT_PROVIDED in power["title"]
    assert "DCGM" not in files["grafana/gpu.json"] + files["alerts.yaml"]


def test_alert_skipped_without_temperature_metric():
    nv = chip_by_name("nvidia-gpu")
    metrics = {k: m for k, m in nv.telemetry.metrics.items() if k != "temperature"}
    chip = nv.model_copy(
        update={"telemetry": nv.telemetry.model_copy(update={"metrics": metrics})}
    )
    rules = yaml.safe_load(
        render_observability(None, GatewayOptions(), chip)["alerts.yaml"]
    )
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    assert "GpuHot" not in names and "EngineDown" in names


def test_exporter_rendered_only_when_image_is_set():
    nv = chip_by_name("nvidia-gpu")
    chip = nv.model_copy(
        update={"telemetry": nv.telemetry.model_copy(update={"exporter_image": None})}
    )
    docs = yaml.safe_load_all(
        render_observability(None, GatewayOptions(), chip)["50-observability.yaml"]
    )
    assert "dcgm-exporter" not in {d["metadata"]["name"] for d in docs}


def test_prometheus_labels_each_series_with_its_pod():
    docs = list(
        yaml.safe_load_all(
            render_observability(None, GatewayOptions())["50-observability.yaml"]
        )
    )
    cm = next(
        d
        for d in docs
        if d["kind"] == "ConfigMap" and "prometheus.yml" in d.get("data", {})
    )
    cfg = yaml.safe_load(cm["data"]["prometheus.yml"])
    relabels = cfg["scrape_configs"][0]["relabel_configs"]
    assert {
        "source_labels": ["__meta_kubernetes_pod_name"],
        "target_label": "pod",
    } in relabels
