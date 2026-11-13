import math
from datetime import UTC, datetime, timedelta

import httpx

TPM = "sum(rate(vllm:prompt_tokens_total[1m]) + rate(vllm:generation_tokens_total[1m])) * 60"
OUTPUT_TPS = "sum(rate(vllm:generation_tokens_total[1m]))"
RUNNING = "vllm:num_requests_running"
WAITING = "vllm:num_requests_waiting"
KV = "vllm:kv_cache_usage_perc"
PROMPT = "rate(vllm:prompt_tokens_total[5m])"
CACHED = "rate(vllm:prompt_tokens_cached_total[5m])"
TTFT_P99 = (
    "histogram_quantile(0.99, "
    "sum(rate(vllm:time_to_first_token_seconds_bucket[1m])) by (le))"
)
ITL_P99 = (
    "histogram_quantile(0.99, "
    "sum(rate(vllm:inter_token_latency_seconds_bucket[1m])) by (le))"
)


class PromError(Exception):
    pass


class Prom:
    def __init__(self, base_url: str, http: httpx.Client):
        self.base_url, self.http = base_url.rstrip("/"), http

    def _get(self, path: str, params: dict) -> list[dict]:
        try:
            r = self.http.get(f"{self.base_url}{path}", params=params, timeout=10)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as err:
            raise PromError(f"Prometheus에 연결하지 못했습니다: {err}") from err
        if body.get("status") != "success":
            raise PromError(f"Prometheus 오류: {body.get('error', body.get('status'))}")
        return body["data"]["result"]

    def instant(self, q: str) -> list[tuple[dict, float]]:
        out = []
        for row in self._get("/api/v1/query", {"query": q}):
            v = float(row["value"][1])
            if math.isfinite(v):
                out.append((row["metric"], v))
        return out

    def range(
        self, q: str, minutes: int, step_s: int
    ) -> list[tuple[dict, list[tuple[datetime, float]]]]:
        end = datetime.now(UTC)
        params = {
            "query": q,
            "start": (end - timedelta(minutes=minutes)).timestamp(),
            "end": end.timestamp(),
            "step": step_s,
        }
        out = []
        for row in self._get("/api/v1/query_range", params):
            series = [
                (datetime.fromtimestamp(float(t), UTC), float(v))
                for t, v in row["values"]
                if math.isfinite(float(v))
            ]
            out.append((row["metric"], series))
        return out
