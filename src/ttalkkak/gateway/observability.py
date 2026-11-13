import json

import yaml

from ttalkkak.chips import ChipConfig, chip_by_name
from ttalkkak.cost.model import CostConfig, CostResult
from ttalkkak.export.common import dump
from ttalkkak.gateway import versions as v
from ttalkkak.gateway.manifests import GatewayOptions

METRICS = {
    "ttalkkak_tokens_total",
    "ttalkkak_requests_total",
    "ttalkkak_tenant_limit_tokens_per_minute",
}
P99 = "histogram_quantile(0.99, sum by (le) (rate({m}_bucket[5m])))"


def _panel(i: int, title: str, exprs: dict[str, str], unit: str = "short") -> dict:
    return {
        "id": i,
        "type": "timeseries",
        "title": title,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"x": (i - 1) % 2 * 12, "y": (i - 1) // 2 * 8, "w": 12, "h": 8},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [
            {"refId": chr(65 + n), "expr": e, "legendFormat": legend}
            for n, (legend, e) in enumerate(exprs.items())
        ],
    }


NOT_PROVIDED = "이 칩은 이 지표를 내지 않음"


def _text_panel(i: int, title: str) -> dict:
    return {
        "id": i,
        "type": "text",
        "title": f"{title} ({NOT_PROVIDED})",
        "gridPos": {"x": (i - 1) % 2 * 12, "y": (i - 1) // 2 * 8, "w": 12, "h": 8},
        "options": {"mode": "markdown", "content": NOT_PROVIDED},
    }


def _dashboard(uid: str, title: str, panels: list[tuple]) -> str:
    return json.dumps(
        {
            "uid": uid,
            "title": title,
            "refresh": "10s",
            "time": {"from": "now-1h", "to": "now"},
            "schemaVersion": 39,
            "panels": [
                _text_panel(i, p[0]) if p[1] is None else _panel(i, *p)
                for i, p in enumerate(panels, 1)
            ],
        },
        indent=1,
        ensure_ascii=False,
    )


def _dashboards(cost: CostResult | None, chip: ChipConfig) -> dict[str, str]:
    m = chip.telemetry.metrics

    def gpu_panel(title: str, key: str, unit: str) -> tuple:
        return (title, {"{{gpu}}": m[key]} if key in m else None, unit)

    cfg = cost.config if cost else CostConfig()
    price = cost.price_krw_per_mtok if cost else None
    krw_per_ws = cfg.electricity_krw_per_kwh * cfg.pue / 3.6e6
    gpu = _dashboard(
        "ttalkkak-gpu",
        "ttalkkak · GPU",
        [
            gpu_panel("사용률", "utilization", "percent"),
            gpu_panel("전력", "power", "watt"),
            gpu_panel("메모리 사용", "memory_used", "decmbytes"),
            gpu_panel("온도", "temperature", "celsius"),
        ],
    )
    serving = _dashboard(
        "ttalkkak-serving",
        "ttalkkak · 서빙",
        [
            (
                "요청",
                {
                    "실행 중": "sum(vllm:num_requests_running)",
                    "대기": "sum(vllm:num_requests_waiting)",
                },
            ),
            (
                "TTFT p99",
                {"p99": P99.format(m="vllm:time_to_first_token_seconds")},
                "s",
            ),
            ("ITL p99", {"p99": P99.format(m="vllm:inter_token_latency_seconds")}, "s"),
            (
                "토큰/초",
                {
                    "입력": "sum(rate(vllm:prompt_tokens_total[1m]))",
                    "출력": "sum(rate(vllm:generation_tokens_total[1m]))",
                },
            ),
            ("KV 캐시 사용률", {"kv": "max(vllm:kv_cache_usage_perc)"}, "percentunit"),
            (
                "접두사 캐시 적중률",
                {
                    "hit": "sum(rate(vllm:prefix_cache_hits_total[5m]))"
                    " / sum(rate(vllm:prefix_cache_queries_total[5m]))"
                },
                "percentunit",
            ),
        ],
    )
    tok = 'sum by (tenant) (increase(ttalkkak_tokens_total{{kind="{k}"}}[1d]))'
    usage_panels = [
        (
            "테넌트별 출력 토큰 (1일)",
            {"{{tenant}}": tok.format(k="output")},
        ),
        (
            "테넌트별 입력 토큰 (1일, 캐시 포함)",
            {"{{tenant}}": tok.format(k="input")},
        ),
        (
            "오류율",
            {
                "{{tenant}}": 'sum by (tenant) (rate(ttalkkak_requests_total{status=~"5.."}[5m]))'
                " / sum by (tenant) (rate(ttalkkak_requests_total[5m]))"
            },
            "percentunit",
        ),
        (
            f"전기료 (원/시간, {cfg.electricity_krw_per_kwh}원/kWh × PUE {cfg.pue})",
            {"원/시간": f"sum({m['power']}) * {krw_per_ws:.10g} * 3600"}
            if "power" in m
            else None,
        ),
    ]
    if price:
        usage_panels.append(
            (
                (
                    f"예상 매출 (원/일, 출력 {price.output:g}원·입력 {price.input:g}원"
                    f"·캐시 {price.cached_input:g}원 /100만 토큰)"
                ),
                {
                    "{{tenant}}": f"({tok.format(k='output')} * {price.output:g}"
                    f" + ({tok.format(k='input')} - {tok.format(k='cached_input')})"
                    f" * {price.input:g}"
                    f" + {tok.format(k='cached_input')} * {price.cached_input:g}) / 1e6"
                },
            )
        )
    usage = _dashboard("ttalkkak-usage", "ttalkkak · 사용량과 비용", usage_panels)
    return {
        "grafana/gpu.json": gpu,
        "grafana/serving.json": serving,
        "grafana/usage.json": usage,
    }


def _alerts(cfg: CostConfig, chip: ChipConfig) -> str:
    def rule(name, expr, summary):
        return {
            "alert": name,
            "expr": expr,
            "for": "5m",
            "labels": {"severity": "warning"},
            "annotations": {"summary": summary},
        }

    ttft, itl = cfg.slo.ttft_p99_ms / 1000, cfg.slo.itl_p99_ms / 1000
    rules = [
        rule(
            "TtftSloBreach",
            f"{P99.format(m='vllm:time_to_first_token_seconds')} > {ttft}",
            "TTFT p99가 SLO를 넘었습니다",
        ),
        rule(
            "ItlSloBreach",
            f"{P99.format(m='vllm:inter_token_latency_seconds')} > {itl}",
            "ITL p99가 SLO를 넘었습니다",
        ),
        rule("KvCacheHigh", "max(vllm:kv_cache_usage_perc) > 0.9", "KV 캐시 90% 초과"),
        rule(
            "TenantQuota80",
            'sum by (tenant) (rate(ttalkkak_tokens_total{kind="total"}[1m])) * 60'
            " > 0.8 * max by (tenant) (ttalkkak_tenant_limit_tokens_per_minute)",
            "테넌트가 분당 토큰 한도의 80%를 넘었습니다",
        ),
        *(
            [rule("GpuHot", f"max({temp}) > 85", "GPU 온도 85°C 초과")]
            if (temp := chip.telemetry.metrics.get("temperature"))
            else []
        ),
        rule(
            "EngineDown",
            "absent(vllm:num_requests_running)",
            "vLLM 지표가 들어오지 않습니다",
        ),
    ]
    return yaml.safe_dump(
        {"groups": [{"name": "ttalkkak", "rules": rules}]},
        sort_keys=False,
        allow_unicode=True,
    )


def _deployment(ns, name, image, args, port, env=(), mounts=(), volumes=(), sa=None):
    labels = {"app.kubernetes.io/name": name}
    spec = {
        "containers": [
            {
                "name": name,
                "image": image,
                "args": list(args),
                "ports": [{"containerPort": port}],
                "env": list(env),
                "volumeMounts": list(mounts),
            }
        ],
        "volumes": list(volumes),
    }
    if sa:
        spec["serviceAccountName"] = sa
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {"metadata": {"labels": labels}, "spec": spec},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": ns},
            "spec": {"selector": labels, "ports": [{"port": port}]},
        },
    ]


def _cm(ns, name, data):
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": ns},
        "data": data,
    }


def _vol(name, cm):
    return {"name": name, "configMap": {"name": cm}}


def _mount(name, path):
    return {"name": name, "mountPath": path}


def _stack(
    opt: GatewayOptions, dashboards: dict[str, str], alerts: str, chip: ChipConfig
) -> list[dict]:
    ns = opt.namespace
    prom_cfg = {
        "global": {"scrape_interval": "10s"},
        "rule_files": ["/etc/prometheus/alerts.yaml"],
        "scrape_configs": [
            {
                "job_name": "pods",
                "kubernetes_sd_configs": [{"role": "pod"}],
                "relabel_configs": [
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_annotation_prometheus_io_scrape"
                        ],
                        "action": "keep",
                        "regex": "true",
                    },
                    {
                        "source_labels": [
                            "__address__",
                            "__meta_kubernetes_pod_annotation_prometheus_io_port",
                        ],
                        "regex": r"([^:]+)(?::\d+)?;(\d+)",
                        "replacement": "$1:$2",
                        "target_label": "__address__",
                    },
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_label_app_kubernetes_io_name"
                        ],
                        "target_label": "app",
                    },
                ],
            },
        ],
    }
    rbac = [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "prometheus", "namespace": ns}},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": f"{ns}-prometheus"},
            "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": f"{ns}-prometheus"},
            "subjects": [{"kind": "ServiceAccount", "name": "prometheus", "namespace": ns}],
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": f"{ns}-prometheus"},
        },
    ]  # fmt: skip
    grafana_ds = {
        "apiVersion": 1,
        "datasources": [
            {
                "name": "Prometheus",
                "uid": "prometheus",
                "type": "prometheus",
                "url": "http://prometheus:9090",
                "isDefault": True,
            },
            {
                "name": "Jaeger",
                "uid": "jaeger",
                "type": "jaeger",
                "url": "http://jaeger:16686",
            },
        ],
    }
    grafana_dash = {
        "apiVersion": 1,
        "providers": [{"name": "ttalkkak", "type": "file", "options": {"path": "/var/lib/grafana/dashboards"}}],
    }  # fmt: skip
    otel_cfg = {
        "receivers": {"otlp": {"protocols": {"grpc": {"endpoint": "0.0.0.0:4317"}}}},
        "processors": {"batch": {}},
        "exporters": {"otlp": {"endpoint": "jaeger:4317", "tls": {"insecure": True}}},
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["batch"],
                    "exporters": ["otlp"],
                }
            }
        },
    }
    dcgm_labels = {"app.kubernetes.io/name": "dcgm-exporter"}
    exporter = (
        [
            {
                "apiVersion": "apps/v1",
                "kind": "DaemonSet",
                "metadata": {"name": "dcgm-exporter", "namespace": ns, "labels": dcgm_labels},
                "spec": {
                    "selector": {"matchLabels": dcgm_labels},
                    "template": {
                        "metadata": {
                            "labels": dcgm_labels,
                            "annotations": {"prometheus.io/scrape": "true", "prometheus.io/port": "9400"},
                        },
                        "spec": {
                            "containers": [{
                                "name": "dcgm-exporter",
                                "image": chip.telemetry.exporter_image,
                                "ports": [{"containerPort": 9400}],
                                "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}},
                                "env": [{"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"}],
                            }],
                        },
                    },
                },
            },
        ]
        if chip.telemetry.exporter_image
        else []
    )  # fmt: skip
    docs = [
        *rbac,
        _cm(ns, "prometheus", {"prometheus.yml": yaml.safe_dump(prom_cfg), "alerts.yaml": alerts}),
        *_deployment(
            ns, "prometheus", v.PROMETHEUS_IMAGE,
            ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=2d"],
            9090, mounts=[_mount("cfg", "/etc/prometheus")],
            volumes=[_vol("cfg", "prometheus")], sa="prometheus",
        ),
        _cm(ns, "grafana-datasources", {"ds.yaml": yaml.safe_dump(grafana_ds)}),
        _cm(ns, "grafana-providers", {"p.yaml": yaml.safe_dump(grafana_dash)}),
        _cm(ns, "grafana-dashboards", {k.split("/")[1]: t for k, t in dashboards.items()}),
        *_deployment(
            ns, "grafana", v.GRAFANA_IMAGE, [], 3000,
            env=[
                {"name": "GF_AUTH_ANONYMOUS_ENABLED", "value": "false"},
                {"name": "GF_SECURITY_ADMIN_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "grafana-admin", "key": "password"}}},
            ],
            mounts=[
                _mount("ds", "/etc/grafana/provisioning/datasources"),
                _mount("providers", "/etc/grafana/provisioning/dashboards"),
                _mount("dashboards", "/var/lib/grafana/dashboards"),
            ],
            volumes=[_vol("ds", "grafana-datasources"), _vol("providers", "grafana-providers"), _vol("dashboards", "grafana-dashboards")],
        ),
        _cm(ns, "otel-collector", {"config.yaml": yaml.safe_dump(otel_cfg)}),
        *_deployment(
            ns, "otel-collector", v.OTEL_COLLECTOR_IMAGE, ["--config=/etc/otel/config.yaml"],
            4317, mounts=[_mount("cfg", "/etc/otel")], volumes=[_vol("cfg", "otel-collector")],
        ),
        *_deployment(ns, "jaeger", v.JAEGER_IMAGE, [], 16686),
        *exporter,
    ]  # fmt: skip
    jaeger_svc = next(
        d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "jaeger"
    )
    jaeger_svc["spec"]["ports"] = [
        {"name": "ui", "port": 16686},
        {"name": "otlp", "port": 4317},
    ]
    return docs


def render_observability(
    cost: CostResult | None, opt: GatewayOptions, chip: ChipConfig | None = None
) -> dict[str, str]:
    chip = chip or chip_by_name("nvidia-gpu")
    cfg = cost.config if cost else CostConfig()
    dashboards = _dashboards(cost, chip)
    alerts = _alerts(cfg, chip)
    return {
        "50-observability.yaml": dump(_stack(opt, dashboards, alerts, chip)),
        **dashboards,
        "alerts.yaml": alerts,
    }
