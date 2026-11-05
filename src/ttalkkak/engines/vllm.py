import ast
import re

import httpx

from ttalkkak.engines.base import ContainerSpec
from ttalkkak.schemas import AppliedConfig, AppliedValue, Candidate

_NON_DEFAULT = re.compile(r"non-default args: (\{.*\})")
_MAX_LEN = re.compile(r"Using max model len (\d+)")
_KV_TOKENS = re.compile(r"GPU KV cache size: ([\d,]+) tokens")
_BACKEND = re.compile(r"Using (\S+) backend\.")
_CACHE_INFO = re.compile(r"vllm:cache_config_info\{([^}]*)\}")
_KEYS = (
    "max_model_len",
    "kv_cache_dtype",
    "kv_cache_tokens",
    "attention_backend",
    "tool_call_parser",
    "reasoning_parser",
    "enable_auto_tool_choice",
)
_FROM_ARGS = (
    "tool_call_parser",
    "reasoning_parser",
    "enable_auto_tool_choice",
    "kv_cache_dtype",
)


def _labels(s: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', s))


def _find(pattern: re.Pattern, text: str) -> tuple[str, str] | None:
    for line in text.splitlines():
        if m := pattern.search(line):
            return m.group(1), line.strip()
    return None


class VllmAdapter:
    name = "vllm"
    container_port = 8000

    def build_args(self, c: Candidate) -> ContainerSpec:
        args = ["--model", c.model_id, "--host", "0.0.0.0", "--port", "8000"]
        for key, val in c.args.items():
            flag = "--" + key.replace("_", "-")
            if val is True:
                args.append(flag)
            elif val is not False:
                args += [flag, str(val)]
        return ContainerSpec(image="", args=args)

    def collect(self, base_url: str, client: httpx.Client) -> dict:
        out: dict = {}
        try:
            r = client.get(f"{base_url}/v1/models", timeout=10)
            if r.status_code == 200:
                out["models"] = r.json()
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = client.get(f"{base_url}/metrics", timeout=10)
            if r.status_code == 200:
                out["metrics"] = r.text
        except httpx.HTTPError:
            pass
        return out

    def read_applied(self, logs: str, collected: dict) -> AppliedConfig:
        metrics = collected.get("metrics", "")
        models = collected.get("models", {})
        v = {k: AppliedValue(value=None, evidence="unknown") for k in _KEYS}

        def put(key, value, evidence):
            v[key] = AppliedValue(value=value, evidence=evidence)

        if hit := _find(_NON_DEFAULT, logs):
            args = ast.literal_eval(hit[0])
            for key in _FROM_ARGS:
                if key in args:
                    put(key, args[key], hit[1])
        if hit := _find(_MAX_LEN, logs):
            put("max_model_len", int(hit[0]), hit[1])
        data = models.get("data") or []
        if data and data[0].get("max_model_len"):
            put(
                "max_model_len",
                int(data[0]["max_model_len"]),
                "/v1/models max_model_len",
            )
        if hit := _find(_KV_TOKENS, logs):
            put("kv_cache_tokens", int(hit[0].replace(",", "")), hit[1])
        if hit := _find(_BACKEND, logs):
            put("attention_backend", hit[0], hit[1])
        if m := _CACHE_INFO.search(metrics):
            lab = _labels(m.group(1))
            if "cache_dtype" in lab:
                put(
                    "kv_cache_dtype",
                    lab["cache_dtype"],
                    "metric vllm:cache_config_info",
                )
        return AppliedConfig(values=v)

    def health(self, base_url: str, client: httpx.Client) -> bool:
        try:
            return client.get(f"{base_url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False
