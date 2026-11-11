"""Pinned external components (checked against release CRDs/sources on 2026-11-11).

Not yet validated on the desktop cluster; see docs/stage5-ledger.md.
"""

ENVOY_GATEWAY = "v1.10.0"
ENVOY_AI_GATEWAY = "v1.2.1"
AI_GATEWAY_API = "aigateway.envoyproxy.io/v1beta1"
ENVOY_GATEWAY_API = "gateway.envoyproxy.io/v1alpha1"
GATEWAY_API = "gateway.networking.k8s.io/v1"
# v1.6.2 is the latest GIE release, but registry.k8s.io only has EPP images up
# to v1.5.0, so the CRDs and the EPP are both pinned to v1.5.0.
GATEWAY_API_INFERENCE_EXT = "v1.5.0"
INFERENCE_POOL_API = "inference.networking.k8s.io/v1"
EPP_IMAGE = "registry.k8s.io/gateway-api-inference-extension/epp:v1.5.0"
DEX_IMAGE = "ghcr.io/dexidp/dex:v2.46.1"
REDIS_IMAGE = "redis:8.10.3"
DCGM_EXPORTER_IMAGE = "nvcr.io/nvidia/k8s/dcgm-exporter:4.9.0"
# vLLM fills usage.prompt_tokens_details.cached_tokens only with this flag.
VLLM_CACHED_USAGE_FLAG = "--enable-prompt-tokens-details"
AI_GATEWAY_METADATA_NS = "io.envoy.ai_gateway"
PROMETHEUS_IMAGE = "prom/prometheus:v3.16.0"
GRAFANA_IMAGE = "grafana/grafana:13.2.6"
OTEL_COLLECTOR_IMAGE = "otel/opentelemetry-collector:0.165.0"
JAEGER_IMAGE = "jaegertracing/jaeger:2.23.0"
