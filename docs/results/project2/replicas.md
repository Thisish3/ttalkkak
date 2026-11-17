# 4절: RTX 3060 Ti 8GB 한 장에 vLLM 복제본이 몇 개까지 뜨는가

- run `20261117-193453`, 모델 `Qwen/Qwen2.5-1.5B-Instruct`(bf16, 가중치 2.88 GiB 체크포인트, 로드 뒤 GPU 2.98 GiB), `--max-model-len 32768`, KV auto, vLLM v0.33.0
- GPU 공유: time-slicing `replicas: 4`(R3), allocatable `nvidia.com/gpu` = 4
- 복제본당 비율 = `floor(0.85 / N × 100) / 100`(`gateway/manifests.py` `memory_fraction`, `--autoscale`이면 N = `--max-replicas`)
- 명령: `.venv/bin/ttalkkak ops export --run 20261117-193453 --target gateway --out deploy/ --replicas N --max-replicas N --autoscale --gpu-share resource --hostname ttalkkak.local --force` 뒤 `.venv/bin/ttalkkak ops cluster up --out deploy/`

| 복제본 수 | 복제본당 메모리 비율 (GiB) | 모두 ready? | 시작 로그의 KV 캐시 | `nvidia-smi` 사용 메모리 | 근거 |
|---|---|---|---|---|---|
| 1 | 0.85 (6.80) | 예 (1/1) | `Available KV cache memory: 2.7 GiB`, `GPU KV cache size: 101,152 tokens`, `Maximum concurrency for 32,768 tokens per request: 3.09x` | 7,033 MiB | `replicas-1.log` |
| 2 (동시에 시작) | 0.42 (3.36) | 아니오 (0/2, 재시작 반복) | `Available KV cache memory: -3.99 GiB` → `ValueError: No available memory for the cache blocks` | 7,600 MiB (재시작 중) | `replicas-2.log` |
| 2 (1개 ready 뒤 2개로) | 0.42 (3.36) | 아니오 (첫 1개부터 실패) | `Available KV cache memory: -0.74 GiB` → 같은 ValueError | 325~328 MiB (실패 뒤) | `replicas-2-sequential.log` |
| 3 | 0.28 (2.24) | 아니오 (0/3, 재시작 반복) | `Available KV cache memory: -1.86 GiB` → 같은 ValueError. 로그: `The current --gpu-memory-utilization=0.2800 is equivalent to --gpu-memory-utilization=0.2356 without CUDA graph memory profiling` | 7,836 MiB (시작 중) | `replicas-3-vllm.log`, `replicas-3-pods.txt` |
| 4 | 0.21 (1.68) | 돌리지 않음 | 3개에서 이미 가중치(2.98 GiB) > 할당(2.24 GiB)이라 생략 (ledger R12) | - | - |

**결론:** 이 모델과 설정에서는 **최대 복제본 수가 1**이다. 2개일 때 할당은 3.36 GiB인데, 가중치 2.98 GiB와 CUDA graph 0.30 GiB(`Graph capturing finished in 11 secs, took 0.30 GiB`, 3개 시험 로그)를 더하면 이미 3.28 GiB다. 거기에 실행 중 활성화 메모리가 더해져 KV 캐시 몫이 음수(-0.74 GiB)가 됐다. 동시에 띄웠을 때 -3.99 GiB로 더 크게 음수가 된 이유는 재지 않았다(원인 미확인).

**모델 다운로드 시간:** 공유 캐시 전에는 파드마다 `Model loading took 2.98 GiB memory and 1088.535065 seconds`가 걸렸다. 공유 캐시(R8) 뒤에는 17.7초(2개 시험), 5.1초(1개 시험)였다.

## AWQ 모델로 다시 잼 (R14): run `20261117-214304`, `Qwen/Qwen2.5-1.5B-Instruct-AWQ`

- `ops up` 결과(`ops_up-awq.log`): c1(KV auto) 선택. KV 토큰 예측/실측 145,914 / 157,200, 출력 214.7 tok/s, TTFT 54.0 ms, 정확도 0.6. c2(fp8)는 정확도 하락 55.0%p로 탈락.
- 각 N은 `ops export ... --replicas N --max-replicas N --autoscale --force` 뒤 `kubectl apply -f deploy/20-vllm.yaml`, `kubectl rollout status --timeout=600s`(`cluster up`이 적용하는 것과 같은 파일).

| 복제본 수 | 복제본당 메모리 비율 (GiB) | 모두 ready? | 시작 로그의 KV 캐시 | `nvidia-smi` 사용 메모리 | 근거 |
|---|---|---|---|---|---|
| 1 | 0.85 (6.80) | 예 (세 번째 시도) | `Model loading took 1.1 GiB`, `Available KV cache memory: 4.6 GiB`, `GPU KV cache size: 172,176 tokens`, `Maximum concurrency for 32,768 tokens per request: 5.25x` | 6,998 MiB | `awq-replicas-1.log` |
| 2 | 0.42 (3.36) | 예 (272초) | 복제본마다 `Model loading took 1.1 GiB`, `Available KV cache memory: 1.16 GiB`, `GPU KV cache size: 43,360 tokens`, `Maximum concurrency for 32,768 tokens per request: 1.32x` | 6,749 MiB | `awq-replicas-2.log` |
| 3 | 0.28 (2.24) | 아니오 | `Available KV cache memory: -0.42 GiB`(다른 파드는 -3.55 GiB) → `ValueError: No available memory for the cache blocks` | 266 MiB (실패 뒤) | `awq-replicas-3.log` |

- AWQ N=1의 처음 두 번(`awq-replicas-1-first.log`, 두 번째는 덮어씀)은 600초 안에 Ready가 되지 않았다(GPU 2,189 MiB, 재시작 없음). 첫 번째는 공유 캐시에 모델을 받던 중이었다(N=2 로그의 `Model loading took ... 173.07 seconds`가 같은 다운로드의 끝으로 보인다). 두 번째가 늦은 이유는 재지 않았다(원인 미확인). 세 번째는 약 2분 만에 Ready가 됐다.
- **결론:** AWQ 모델은 이 GPU에 **최대 2개**까지 뜬다. 이후 7–9절은 `--replicas 2 --max-replicas 2 --autoscale`(비율 0.42, 복제본 1개일 때도 같은 비율)로 하고, 7절 비교는 1개 vs 2개다(R14).
