import re

import httpx

from ttalkkak.engines.base import ContainerSpec
from ttalkkak.schemas import AppliedConfig, AppliedValue, Candidate

_FLAGS = {
    "max_model_len": "--context-length",
    "gpu_memory_utilization": "--mem-fraction-static",
    "tool_call_parser": "--tool-call-parser",
    "reasoning_parser": "--reasoning-parser",
}
_KV_TO_ENGINE = {"auto": "auto", "fp8": "fp8_e4m3"}
_KV_FROM_ENGINE = {
    "fp8_e4m3": "fp8",
    "fp8_e5m2": "fp8",
    "auto": "auto",
    "bf16": "auto",
    "bfloat16": "auto",
}
_LOG_TOKENS = re.compile(r"max_total_num_tokens=(\d+)")
_LOG_CTX = re.compile(r"context_len=(\d+)")
_KEYS = (
    "max_model_len",
    "kv_cache_dtype",
    "kv_cache_tokens",
    "attention_backend",
    "tool_call_parser",
    "reasoning_parser",
)
_INFO_FIELDS = (
    ("max_model_len", "context_length"),
    ("tool_call_parser", "tool_call_parser"),
    ("reasoning_parser", "reasoning_parser"),
    ("attention_backend", "attention_backend"),
)


def _find(pattern: re.Pattern, text: str) -> tuple[str, str] | None:
    for line in text.splitlines():
        if m := pattern.search(line):
            return m.group(1), line.strip()
    return None


class SglangAdapter:
    name = "sglang"
    container_port = 30000

    def build_args(self, c: Candidate) -> ContainerSpec:
        args = [
            "python3", "-m", "sglang.launch_server",
            "--model-path", c.model_id,
            "--host", "0.0.0.0",
            "--port", str(self.container_port),
            "--enable-metrics",
        ]  # fmt: skip
        for key, val in c.args.items():
            if key == "kv_cache_dtype":
                args += ["--kv-cache-dtype", _KV_TO_ENGINE[str(val)]]
            elif key in _FLAGS and val not in (None, False):
                args += [_FLAGS[key], str(val)]
        return ContainerSpec(image="", args=args)

    def collect(self, base_url: str, client: httpx.Client) -> dict:
        try:
            r = client.get(f"{base_url}/server_info", timeout=10)
            if r.status_code == 200:
                return {"server_info": r.json()}
        except (httpx.HTTPError, ValueError):
            pass
        return {}

    def read_applied(self, logs: str, collected: dict) -> AppliedConfig:
        v = {k: AppliedValue(value=None, evidence="unknown") for k in _KEYS}
        info = collected.get("server_info") or {}
        for key, field in _INFO_FIELDS:
            if info.get(field) is not None:
                v[key] = AppliedValue(
                    value=info[field], evidence=f"/server_info {field}"
                )
        if info.get("kv_cache_dtype") is not None:
            raw = str(info["kv_cache_dtype"])
            v["kv_cache_dtype"] = AppliedValue(
                value=_KV_FROM_ENGINE.get(raw, raw),
                evidence=f"/server_info kv_cache_dtype={raw}",
            )
        states = info.get("internal_states") or []
        capacity = (
            ((states[0] or {}).get("memory_usage") or {}).get("token_capacity")
            if states
            else None
        )
        if info.get("max_total_num_tokens") is not None:
            v["kv_cache_tokens"] = AppliedValue(
                value=int(info["max_total_num_tokens"]),
                evidence="/server_info max_total_num_tokens",
            )
        elif capacity is not None:
            v["kv_cache_tokens"] = AppliedValue(
                value=int(capacity),
                evidence="/server_info internal_states[0].memory_usage.token_capacity",
            )
        if v["kv_cache_tokens"].value is None and (hit := _find(_LOG_TOKENS, logs)):
            v["kv_cache_tokens"] = AppliedValue(value=int(hit[0]), evidence=hit[1])
        if v["max_model_len"].value is None and (hit := _find(_LOG_CTX, logs)):
            v["max_model_len"] = AppliedValue(value=int(hit[0]), evidence=hit[1])
        return AppliedConfig(values=v)

    def health(self, base_url: str, client: httpx.Client) -> bool:
        try:
            return client.get(f"{base_url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False
