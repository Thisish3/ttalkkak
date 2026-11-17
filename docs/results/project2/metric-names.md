# 6.1 vLLM 지표 이름 대조

- 실제 이름 목록: `vllm-metric-names.txt`(104개, 이름만)

| `src/ttalkkak/live/prom.py`가 쓰는 이름 | 실제 /metrics에 그대로 있음 |
|---|---|
| `vllm:generation_tokens_total` | 예 |
| `vllm:inter_token_latency_seconds_bucket` | 예 |
| `vllm:kv_cache_usage_perc` | 예 |
| `vllm:num_requests_running` | 예 |
| `vllm:num_requests_waiting` | 예 |
| `vllm:prompt_tokens_cached_total` | 예 |
| `vllm:prompt_tokens_total` | 예 |
| `vllm:time_to_first_token_seconds_bucket` | 예 |

결론: 8개가 모두 그대로 있다. 코드는 고치지 않았다.
