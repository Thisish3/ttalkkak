# 6.2 게이트웨이 429와 고객별 토큰: 콘솔 vs Envoy 접근 로그

원본: `limit-429.log`. 키는 파일에서 읽어 쓰고 출력하지 않았다. 끝난 뒤 키를 폐기하고 한도를 원래대로 돌렸다.

## 순서
1. `ttalkkak ops tenants set-limit acme 200`(원래 한도 없음), acme 키 발급(파일로만 받음), `ops export ... --force`, `kubectl apply -f deploy/00-namespace.yaml -f deploy/25-ops-serve.yaml -f deploy/40-gateway.yaml`
2. 첫 시도(13:39:36Z): 요청 15개가 모두 **500**(약 6 ms). Envoy Gateway가 경로에 500 직접 응답을 넣고 있었다.
   - `Lua: Skipping Lua EnvoyExtensionPolicy as feature is disabled in the Gateway`(`ttalkkak-cache-salt`)
   - `Group is invalid, only the core API group ..., multicluster.x-k8s.io and gateway.envoyproxy.io are supported`(InferencePool backendRef)
   - EG 설정을 AI Gateway v1.2.1 값으로 다시 설치해 고쳤다(R19, `envoy-gateway-values.yaml`, `envoy-gateway-upgrade.log`).
3. 다시 시도(13:42:28Z): `POST /v1/chat/completions` 15개(`max_tokens` 64) → **200 두 번(각 total_tokens 102), 그다음 429 열세 번**
4. 비교(22:43:11 KST)

| 항목 | Envoy 접근 로그 (`kubectl logs ... -c envoy --since-time=2026-11-17T13:42:00Z`) | 콘솔 (`/api/live`) | 같은가 |
|---|---|---|---|
| 429 수 | `"status":429` 13줄 | `signals.rejected_429_1h: 13`, acme `throttled_1h: 13` | 같음 |
| acme 토큰 | 200 두 줄: `input_tokens` 38 + `output_tokens` 64 = 102씩, 합계 204 | acme `tokens_last_min: 204`, `limit_tpm: 200`, `pct: 102.0` | 같음 |
| 두 번째 요청의 캐시 | `cached_input_tokens: 32` | (실시간 탭 항목 아님) | - |

- 두 번째 200까지 통과하고 세 번째부터 막힌 것은 한도 비용이 응답의 `total_tokens`로 계산되기 때문이다(`BackendTrafficPolicy` `cost.response.metadata.key: total_tokens`). 첫 응답 뒤 102 < 200이라 두 번째도 통과했다.
- 복구: `set-limit acme none`, 키 폐기(`<접두어> 키를 폐기했습니다`), export와 apply를 다시 했다. `tenants list`에서 acme 한도는 `-`, 활성 키 1개.

## 이때 함께 찾은 콘솔 문제(R20)
- `/api/live`가 `ready_replicas: 3`(최대 2)을 보였고, 복제본 표에 EPP 파드가 들어 있었다. 복제본별 `running`/`waiting`/`kv`는 모두 `null`이었다.
- 고친 뒤(0955a46): 복제본 표에 vLLM 파드 2개만 나오고, 값이 채워진다(`running: 0.0, waiting: 0.0, kv: 0.0`).
