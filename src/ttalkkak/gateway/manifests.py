import json
import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from ttalkkak.chips import chip_by_name, chip_from_ref
from ttalkkak.cost.model import CostResult
from ttalkkak.errors import TtalkkakError
from ttalkkak.export.common import dump, gpu_key, load_run, resource_name
from ttalkkak.export.k8s import render as render_k8s
from ttalkkak.gateway import versions as v
from ttalkkak.tenants.store import TenantStore

CLI_CLIENT_ID = "ttalkkak-cli"
OPS_SERVICE, OPS_PORT = "ttalkkak-ops", 8790
TENANT_HEADERS = ["x-ttalkkak-tenant", "x-ttalkkak-subject", "x-ttalkkak-cache-salt"]
COSTS = {
    "input_tokens": "InputToken",
    "cached_input_tokens": "CachedInputToken",
    "output_tokens": "OutputToken",
    "total_tokens": "TotalToken",
}
LUA_CACHE_SALT = """\
function envoy_on_request(h)
  local salt = h:headers():get("x-ttalkkak-cache-salt")
  h:headers():remove("x-ttalkkak-cache-salt")
  if salt == nil or not salt:match("^%x+$") then return end
  local body = h:body(true)
  if body == nil or body:length() == 0 then return end
  local s = body:getBytes(0, body:length())
  local i = s:match("^.*()}")
  if i == nil then return end
  local head = s:sub(1, i - 1)
  local sep = head:match("{%s*$") and "" or ","
  local out = head .. sep .. '"cache_salt":"' .. salt .. '"' .. s:sub(i)
  body:setBytes(out)
  h:headers():replace("content-length", tostring(#out))
end
"""


class GatewayOptions(BaseModel):
    namespace: str = "ttalkkak"
    replicas: int = 1
    issuer: str | None = None
    hostname: str = "ttalkkak.local"
    image: str | None = None
    dex_users: list[dict] = []
    chip: str | None = None
    max_replicas: int = 1
    autoscale: bool = True
    gpu_share: Literal["resource", "runtime"] = "resource"


GPU_MEMORY_BUDGET = 0.85
NVIDIA_RESOURCE = "nvidia.com/gpu"


def memory_fraction(opt: GatewayOptions) -> float:
    n = opt.max_replicas if opt.autoscale else opt.replicas
    return math.floor(GPU_MEMORY_BUDGET / max(n, 1) * 100) / 100


def _meta(name: str, ns: str, **labels) -> dict:
    m = {"name": name, "namespace": ns}
    if labels:
        m["labels"] = labels
    return m


def issuer_of(opt: GatewayOptions) -> str:
    return opt.issuer or f"https://{opt.hostname}/dex"


def tenants_json(store: TenantStore) -> str:
    tenants = store.list_tenants()
    return json.dumps(
        {
            "tenants": [t.id for t in tenants],
            "keys": store.key_hashes(),
            "salts": {t.id: s for t in tenants if (s := store.cache_salt(t.id))},
            "limits": {
                t.id: t.token_limit_per_min for t in tenants if t.token_limit_per_min
            },
        },
        sort_keys=True,
    )


def _namespace(store: TenantStore, opt: GatewayOptions) -> list[dict]:
    ns = opt.namespace
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}},
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": _meta("ttalkkak-tenants", ns),
            "type": "Opaque",
            "stringData": {"tenants.json": tenants_json(store)},
        },
    ]


def _redis(opt: GatewayOptions) -> list[dict]:
    ns, labels = opt.namespace, {"app.kubernetes.io/name": "redis"}
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _meta("redis", ns, **labels),
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "containers": [
                            {
                                "name": "redis",
                                "image": v.REDIS_IMAGE,
                                "args": ["--save", "", "--appendonly", "no"],
                                "ports": [{"containerPort": 6379}],
                            }
                        ]
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _meta("redis", ns),
            "spec": {"selector": labels, "ports": [{"port": 6379}]},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta("redis-from-envoy-gateway", ns),
            "spec": {
                "podSelector": {"matchLabels": labels},
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": (
                                            "envoy-gateway-system"
                                        )
                                    }
                                }
                            }
                        ]
                    }
                ],
            },
        },
    ]


def _dex(opt: GatewayOptions) -> list[dict]:
    ns, labels = opt.namespace, {"app.kubernetes.io/name": "dex"}
    config = {
        "issuer": issuer_of(opt),
        "storage": {"type": "memory"},
        "web": {"http": "0.0.0.0:5556"},
        "oauth2": {
            "grantTypes": [
                "urn:ietf:params:oauth:grant-type:device_code",
                "refresh_token",
            ],
            "skipApprovalScreen": True,
        },
        "staticClients": [
            {"id": CLI_CLIENT_ID, "name": "ttalkkak CLI", "public": True}
        ],
        "enablePasswordDB": True,
        "staticPasswords": opt.dex_users,
    }
    return [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": _meta("dex", ns),
            "type": "Opaque",
            "stringData": {"config.yaml": dump([config])},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _meta("dex", ns, **labels),
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "containers": [
                            {
                                "name": "dex",
                                "image": v.DEX_IMAGE,
                                "args": ["dex", "serve", "/etc/dex/config.yaml"],
                                "ports": [{"containerPort": 5556, "name": "http"}],
                                "volumeMounts": [
                                    {"name": "config", "mountPath": "/etc/dex"}
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "config", "secret": {"secretName": "dex"}}
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _meta("dex", ns),
            "spec": {"selector": labels, "ports": [{"name": "http", "port": 5556}]},
        },
    ]


def _vllm(run_dir: Path, opt: GatewayOptions) -> tuple[list[dict], str, dict]:
    b = load_run(run_dir)
    if opt.chip:
        b = b.model_copy(update={"vendor": chip_from_ref(opt.chip).name})
    files = render_k8s(b, opt.image)
    (dep,) = yaml.safe_load_all(files["deployment.yaml"])
    (svc,) = yaml.safe_load_all(files["service.yaml"])
    for d in (dep, svc):
        d["metadata"]["namespace"] = opt.namespace
    dep["spec"]["replicas"] = opt.replicas
    tmpl = dep["spec"]["template"]
    container = tmpl["spec"]["containers"][0]
    port = container["ports"][0]["containerPort"]
    tmpl["metadata"]["annotations"] = {
        "prometheus.io/scrape": "true",
        "prometheus.io/port": str(port),
    }
    args = container["args"]
    fraction = str(memory_fraction(opt))
    if "--gpu-memory-utilization" in args:
        args[args.index("--gpu-memory-utilization") + 1] = fraction
    else:
        args += ["--gpu-memory-utilization", fraction]
    if opt.gpu_share == "runtime":
        if gpu_key(b.vendor) != NVIDIA_RESOURCE:
            raise TtalkkakError("이 칩은 runtime 공유를 지원하지 않습니다", 2)
        del container["resources"]
        container.setdefault("env", []).append(
            {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"}
        )
        tmpl["spec"]["runtimeClassName"] = "nvidia"
    if v.VLLM_CACHED_USAGE_FLAG not in container["args"]:
        container["args"].append(v.VLLM_CACHED_USAGE_FLAG)
    labels = {"app.kubernetes.io/name": dep["metadata"]["name"]}
    return [dep, svc], resource_name(b), {"labels": labels, "port": port}


def _autoscale(opt: GatewayOptions, deployment: str) -> list[dict]:
    ns = opt.namespace
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _meta("ttalkkak-autoscale", ns),
            "data": {
                "enabled": "true" if opt.autoscale else "false",
                "max_replicas": str(opt.max_replicas),
                "deployment": deployment,
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _meta("ttalkkak-capacity", ns),
            "data": {"capacity.json": "{}"},
        },
    ]


def _llmd(name: str, ns: str, pool: dict) -> list[dict]:
    epp, labels = f"{name}-epp", {"app.kubernetes.io/name": f"{name}-epp"}
    return [
        {
            "apiVersion": v.INFERENCE_POOL_API,
            "kind": "InferencePool",
            "metadata": _meta(name, ns),
            "spec": {
                "selector": {"matchLabels": pool["labels"]},
                "targetPorts": [{"number": pool["port"]}],
                "endpointPickerRef": {"name": epp, "port": {"number": 9002}},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": _meta(epp, ns),
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": _meta(epp, ns),
            "rules": [
                {
                    "apiGroups": ["inference.networking.k8s.io"],
                    "resources": ["inferencepools"],
                    "verbs": ["get", "watch", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "watch", "list"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": _meta(epp, ns),
            "subjects": [{"kind": "ServiceAccount", "name": epp, "namespace": ns}],
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": epp,
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _meta(epp, ns, **labels),
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "prometheus.io/scrape": "true",
                            "prometheus.io/port": "9090",
                        },
                    },
                    "spec": {
                        "serviceAccountName": epp,
                        "containers": [
                            {
                                "name": "epp",
                                "image": v.EPP_IMAGE,
                                "args": [
                                    "--pool-name",
                                    name,
                                    "--pool-namespace",
                                    ns,
                                    "--grpc-port",
                                    "9002",
                                    "--grpc-health-port",
                                    "9003",
                                    "--metrics-port",
                                    "9090",
                                ],
                                "ports": [
                                    {"containerPort": 9002, "name": "grpc"},
                                    {"containerPort": 9090, "name": "metrics"},
                                ],
                                "readinessProbe": {
                                    "grpc": {
                                        "port": 9003,
                                        "service": "inference-extension",
                                    }
                                },
                            }
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _meta(epp, ns),
            "spec": {
                "selector": labels,
                "ports": [{"name": "grpc", "port": 9002, "appProtocol": "http2"}],
            },
        },
    ]


def _access_log() -> dict:
    md = v.AI_GATEWAY_METADATA_NS
    fields = {
        "ts": "%START_TIME%",
        "request_id": "%REQ(X-REQUEST-ID)%",
        "tenant": "%REQ(X-TTALKKAK-TENANT)%",
        "subject": "%REQ(X-TTALKKAK-SUBJECT)%",
        "model": "%REQ(X-AI-EG-MODEL)%",
        "status": "%RESPONSE_CODE%",
        "duration_ms": "%DURATION%",
        **{k: f"%DYNAMIC_METADATA({md}:{k})%" for k in COSTS if k != "total_tokens"},
    }
    return {
        "settings": [
            {
                "format": {"type": "JSON", "json": fields},
                "sinks": [{"type": "File", "file": {"path": "/dev/stdout"}}],
            }
        ]
    }


def _gateway(store: TenantStore, opt: GatewayOptions, pool: str, ns: str) -> list[dict]:
    route_ref = [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute"}]
    gw_ref = {
        "group": "gateway.networking.k8s.io",
        "kind": "Gateway",
        "name": "ttalkkak",
    }
    rules = [
        {
            "clientSelectors": [
                {
                    "headers": [
                        {"name": "x-ttalkkak-tenant", "type": "Exact", "value": t.id}
                    ]
                }
            ],
            "limit": {"requests": t.token_limit_per_min, "unit": "Minute"},
            "cost": {
                "request": {"from": "Number", "number": 0},
                "response": {
                    "from": "Metadata",
                    "metadata": {
                        "namespace": v.AI_GATEWAY_METADATA_NS,
                        "key": "total_tokens",
                    },
                },
            },
        }
        for t in store.list_tenants()
        if t.token_limit_per_min
    ]
    docs = [
        {
            "apiVersion": v.ENVOY_GATEWAY_API,
            "kind": "EnvoyProxy",
            "metadata": _meta("ttalkkak", ns),
            "spec": {"telemetry": {"accessLog": _access_log()}},
        },
        {
            "apiVersion": v.GATEWAY_API,
            "kind": "GatewayClass",
            "metadata": {"name": "ttalkkak"},
            "spec": {
                "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
                "parametersRef": {
                    "group": "gateway.envoyproxy.io",
                    "kind": "EnvoyProxy",
                    "name": "ttalkkak",
                    "namespace": ns,
                },
            },
        },
        {
            "apiVersion": v.GATEWAY_API,
            "kind": "Gateway",
            "metadata": _meta("ttalkkak", ns),
            "spec": {
                "gatewayClassName": "ttalkkak",
                "listeners": [
                    {
                        "name": "https",
                        "protocol": "HTTPS",
                        "port": 443,
                        "hostname": opt.hostname,
                        "tls": {"certificateRefs": [{"name": "ttalkkak-tls"}]},
                    }
                ],
            },
        },
        {
            "apiVersion": v.AI_GATEWAY_API,
            "kind": "AIGatewayRoute",
            "metadata": _meta("ttalkkak", ns),
            "spec": {
                "parentRefs": [{"name": "ttalkkak"}],
                "rules": [
                    {
                        "matches": [],
                        "backendRefs": [
                            {
                                "group": "inference.networking.k8s.io",
                                "kind": "InferencePool",
                                "name": pool,
                            }
                        ],
                    }
                ],
                "llmRequestCosts": [
                    {"metadataKey": k, "type": t} for k, t in COSTS.items()
                ],
            },
        },
        {
            "apiVersion": v.GATEWAY_API,
            "kind": "HTTPRoute",
            "metadata": _meta("ttalkkak-public", ns),
            "spec": {
                "parentRefs": [{"name": "ttalkkak"}],
                "rules": [
                    {
                        "matches": [
                            {"path": {"type": "Exact", "value": p}}
                            for p in ("/v1/pricing", "/v1/auth-config")
                        ],
                        "backendRefs": [{"name": OPS_SERVICE, "port": OPS_PORT}],
                    },
                    *(
                        []
                        if opt.issuer
                        else [
                            {
                                "matches": [
                                    {"path": {"type": "PathPrefix", "value": "/dex"}}
                                ],
                                "backendRefs": [{"name": "dex", "port": 5556}],
                            }
                        ]
                    ),
                ],
            },
        },
        {
            "apiVersion": v.GATEWAY_API,
            "kind": "HTTPRoute",
            "metadata": _meta("ttalkkak-usage", ns),
            "spec": {
                "parentRefs": [{"name": "ttalkkak"}],
                "rules": [
                    {
                        "matches": [{"path": {"type": "Exact", "value": "/v1/usage"}}],
                        "backendRefs": [{"name": OPS_SERVICE, "port": OPS_PORT}],
                    }
                ],
            },
        },
        {
            "apiVersion": v.ENVOY_GATEWAY_API,
            "kind": "SecurityPolicy",
            "metadata": _meta("ttalkkak-auth", ns),
            "spec": {
                "targetRefs": [
                    {**route_ref[0], "name": "ttalkkak"},
                    {**route_ref[0], "name": "ttalkkak-usage"},
                ],
                "extAuth": {
                    "headersToExtAuth": ["authorization"],
                    "failOpen": False,
                    "http": {
                        "backendRefs": [{"name": OPS_SERVICE, "port": OPS_PORT}],
                        "path": "/authz",
                        "headersToBackend": TENANT_HEADERS,
                    },
                },
            },
        },
        {
            "apiVersion": v.ENVOY_GATEWAY_API,
            "kind": "EnvoyExtensionPolicy",
            "metadata": _meta("ttalkkak-cache-salt", ns),
            "spec": {
                "targetRefs": [{**route_ref[0], "name": "ttalkkak"}],
                "lua": [{"type": "Inline", "inline": LUA_CACHE_SALT}],
            },
        },
        {
            "apiVersion": v.ENVOY_GATEWAY_API,
            "kind": "BackendTrafficPolicy",
            "metadata": _meta("ttalkkak-limits", ns),
            "spec": {
                "targetRefs": [gw_ref],
                "rateLimit": {"type": "Global", "global": {"rules": rules}},
            },
        },
    ]
    return docs


OPS_IMAGE = "ttalkkak:local"


def _ops_serve(opt: GatewayOptions, cost: CostResult | None) -> list[dict]:
    ns, labels = opt.namespace, {"app.kubernetes.io/name": OPS_SERVICE}
    jwks = (
        f"http://dex.{ns}.svc:5556/dex/keys"
        if opt.issuer is None
        else f"{opt.issuer.rstrip('/')}/keys"
    )
    args = [
        "ops", "serve", "--host", "0.0.0.0", "--port", str(OPS_PORT),
        "--tenants-json", "/etc/ttalkkak/tenants/tenants.json",
        "--cost", "/etc/ttalkkak/cost/cost.json",
        "--issuer", issuer_of(opt), "--client-id", CLI_CLIENT_ID,
        "--jwks-url", jwks, "--logs-from-cluster",
        "--prometheus-url", f"http://prometheus.{ns}.svc:9090",
        "--autoscale-namespace", ns,
    ]  # fmt: skip
    pod_reader = "ttalkkak-envoy-log-reader"
    scaler = "ttalkkak-ops-autoscale"
    return [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": _meta(OPS_SERVICE, ns)},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": _meta(pod_reader, "envoy-gateway-system"),
            "rules": [
                {"apiGroups": [""], "resources": ["pods", "pods/log"], "verbs": ["get", "list"]}
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": _meta(pod_reader, "envoy-gateway-system"),
            "subjects": [{"kind": "ServiceAccount", "name": OPS_SERVICE, "namespace": ns}],
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": pod_reader},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": _meta(scaler, ns),
            "rules": [
                {"apiGroups": ["apps"], "resources": ["deployments/scale"], "verbs": ["get", "patch"]},
                {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "list"]},
                {
                    "apiGroups": [""],
                    "resources": ["configmaps"],
                    "verbs": ["get"],
                    "resourceNames": ["ttalkkak-autoscale", "ttalkkak-capacity"],
                },
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": _meta(scaler, ns),
            "subjects": [{"kind": "ServiceAccount", "name": OPS_SERVICE, "namespace": ns}],
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": scaler},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _meta("ttalkkak-cost", ns),
            "data": {"cost.json": cost.model_dump_json() if cost else "{}"},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": _meta(OPS_SERVICE, ns, **labels),
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "prometheus.io/scrape": "true",
                            "prometheus.io/port": str(OPS_PORT),
                        },
                    },
                    "spec": {
                        "serviceAccountName": OPS_SERVICE,
                        "containers": [
                            {
                                "name": "ops",
                                "image": OPS_IMAGE,
                                "imagePullPolicy": "IfNotPresent",
                                "args": args,
                                "ports": [{"containerPort": OPS_PORT}],
                                "readinessProbe": {
                                    "httpGet": {"path": "/v1/auth-config", "port": OPS_PORT}
                                },
                                "volumeMounts": [
                                    {"name": "tenants", "mountPath": "/etc/ttalkkak/tenants", "readOnly": True},
                                    {"name": "cost", "mountPath": "/etc/ttalkkak/cost", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "tenants", "secret": {"secretName": "ttalkkak-tenants"}},
                            {"name": "cost", "configMap": {"name": "ttalkkak-cost"}},
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _meta(OPS_SERVICE, ns),
            "spec": {"selector": labels, "ports": [{"port": OPS_PORT}]},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(f"{OPS_SERVICE}-ingress", ns),
            "spec": {
                "podSelector": {"matchLabels": labels},
                "policyTypes": ["Ingress"],
                "ingress": [{"from": [
                    {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "envoy-gateway-system"}}},
                    {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}},
                ]}],
            },
        },
    ]  # fmt: skip


def render_stack(
    run_dir: Path,
    store: TenantStore,
    cost: CostResult | None,
    opt: GatewayOptions,
) -> dict[str, str]:
    if opt.replicas > opt.max_replicas:
        raise TtalkkakError(
            f"복제본 수({opt.replicas})가 최대 복제본 수({opt.max_replicas})보다 큽니다",
            2,
        )
    if cost is not None and cost.gpus_needed and opt.replicas == 1:
        replicas = cost.gpus_needed
        opt = opt.model_copy(
            update={
                "replicas": replicas,
                "max_replicas": max(opt.max_replicas, replicas),
            }
        )
    ns = opt.namespace
    vllm, name, pool = _vllm(run_dir, opt)
    files = {
        "00-namespace.yaml": dump(_namespace(store, opt)),
        "10-redis.yaml": dump(_redis(opt)),
        "20-vllm.yaml": dump(vllm + _autoscale(opt, name)),
        "25-ops-serve.yaml": dump(_ops_serve(opt, cost)),
        "30-llmd.yaml": dump(_llmd(name, ns, pool)),
        "40-gateway.yaml": dump(_gateway(store, opt, name, ns)),
    }
    if opt.issuer is None:
        files["15-dex.yaml"] = dump(_dex(opt))
    from ttalkkak.gateway.observability import render_observability

    chip = (
        chip_from_ref(opt.chip) if opt.chip else chip_by_name(load_run(run_dir).vendor)
    )
    files.update(render_observability(cost, opt, chip))
    files["README.md"] = README.format(
        ns=ns,
        host=opt.hostname,
        eg=v.ENVOY_GATEWAY,
        aieg=v.ENVOY_AI_GATEWAY,
        gie=v.GATEWAY_API_INFERENCE_EXT,
        issuer=issuer_of(opt),
    )
    return dict(sorted(files.items()))


README = """# ttalkkak gateway stack

| file | contents |
|---|---|
| 00-namespace.yaml | namespace, tenant key hashes and cache salts (Secret) |
| 10-redis.yaml | Redis for global token rate limits (reachable from envoy-gateway-system only) |
| 15-dex.yaml | Dex login server at {issuer} (absent with --issuer) |
| 20-vllm.yaml | vLLM Deployment + Service, autoscale and capacity ConfigMaps |
| 30-llmd.yaml | InferencePool + endpoint picker (EPP) |
| 25-ops-serve.yaml | ttalkkak ops serve: /authz, /v1/pricing, /v1/usage, /metrics |
| 40-gateway.yaml | Gateway, AIGatewayRoute (token costs), auth, cache salt, limits |
| 50-observability.yaml | Prometheus (+alerts.yaml), Grafana (grafana/*.json), OTel Collector, Jaeger, DCGM exporter |

Prerequisites (versions pinned in ttalkkak/gateway/versions.py):

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/{gie}/manifests.yaml
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm --version {eg} \\
  -n envoy-gateway-system --create-namespace \\
  --set config.envoyGateway.rateLimit.backend.type=Redis \\
  --set config.envoyGateway.rateLimit.backend.redis.url=redis.{ns}.svc.cluster.local:6379
helm upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm --version {aieg} \\
  -n envoy-ai-gateway-system --create-namespace
helm upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm --version {aieg} \\
  -n envoy-ai-gateway-system
kubectl create namespace {ns}
kubectl -n {ns} create secret tls ttalkkak-tls --cert=tls.crt --key=tls.key  # for {host}
docker build -t ttalkkak:local . && docker save ttalkkak:local | sudo k3s ctr images import -
kubectl -n {ns} create secret generic grafana-admin --from-literal=password=<choose one>
kubectl -n {ns} create secret generic hf-token --from-literal=HF_TOKEN=$HF_TOKEN
```

Then `ttalkkak ops cluster up --out <this dir>` or `kubectl apply -f` in file order.
"""
