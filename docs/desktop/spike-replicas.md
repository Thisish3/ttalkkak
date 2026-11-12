# 실험: GPU 한 장에서 복제본 1개 vs 3개 (프로젝트 2 설계 전 확인)

**성격:** 설계를 정하기 위한 짧은 측정이다(약 30~60분). 제품 코드는 고치지 않는다. 결과 숫자와 그림만 남긴다.

**왜 하나:** 프로젝트 2 "몰려도 버티기"의 시연 장면은 "복제본 1개일 때와 3개일 때 비교"다(사용자 결정). 그런데 GPU 한 장에서는 vLLM 복제본 하나도 요청을 묶어 처리(continuous batching)하고, 복제본 3개는 GPU 계산과 메모리를 나눠 쓴다. 그래서 3개가 1개보다 빠르지 않을 수 있다. 어느 쪽인지 재 보고 설계를 정한다.

**순서:** `stage5-desktop.md`(프로젝트 1)와 독립이다. 데스크탑이 켜지면 이것을 먼저 해도 된다. k3s나 게이트웨이 없이 Docker로만 한다.

## 조건

- 모델: `Qwen/Qwen2.5-1.5B-Instruct` (제품 정의서의 "한 구성")
- 엔진: vLLM, ttalkkak `platform.py`에 고정된 이미지(`vllm/vllm-openai:v0.33.0`)
- SM 클럭: 고정한다(`nvidia-smi -lgc 1500,1500`, Windows 관리자). 사용자에게 요청하고 기다린다. 측정 전후 클럭을 기록한다.
- 부하: guidellm 0.8.1(ttalkkak `measure` extra), 입력 256 / 출력 128 토큰, 동시 사용자(concurrency) 1, 5, 10, 20, 단계마다 60초

## 구성 A: 복제본 1개

```bash
docker run -d --name r1 --gpus all -p 8001:8000 vllm/vllm-openai:v0.33.0 \
  --model Qwen/Qwen2.5-1.5B-Instruct --gpu-memory-utilization 0.85 --max-model-len 4096
```

## 구성 B: 복제본 3개 (같은 GPU)

- 메모리를 셋이 나눈다. 각 0.27(합 0.81)로 시작하고, 뜨지 않으면 값을 낮추며 기록한다.
- 포트 8001, 8002, 8003.
- 앞단 분배: **가장 단순한 라운드로빈**으로 한다. nginx 컨테이너 하나, upstream 3개, 8000 포트.
  - llm-d나 게이트웨이는 쓰지 않는다. 이 실험은 "복제본 수"의 효과만 본다.

```bash
for i in 1 2 3; do docker run -d --name r$i --gpus all -p 800$i:8000 vllm/vllm-openai:v0.33.0 \
  --model Qwen/Qwen2.5-1.5B-Instruct --gpu-memory-utilization 0.27 --max-model-len 4096; done
```

## 측정 명령 (A는 8001, B는 nginx 8000을 대상으로)

```bash
guidellm run --backend kind=openai_http,target=http://localhost:<포트>,model=Qwen/Qwen2.5-1.5B-Instruct \
  --profile kind=concurrent --override profile.streams 1,5,10,20 \
  --constraint kind=max_duration,seconds=60 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --tokenizer kind=hf_auto,model=Qwen/Qwen2.5-1.5B-Instruct \
  --output kind=json,path=<결과 파일> --disable-console-interactive --disable-progress
```

각 구성마다 기록할 것:
- vLLM 시작 로그의 `GPU KV cache size`
- `nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv`(부하 중)

## 결과로 남길 것

`docs/results/spike-replicas/`에 저장하고 커밋, push한다(브랜치 `stage5`).

1. **표**: 동시 사용자 수 × 구성(1개 / 3개)마다 다음 값.
   - TTFT p50·p99
   - ITL p50·p99
   - 출력 토큰/초 (전체 합)
   - 성공률
2. **그림 1장**: 가로축 동시 사용자 수, 세로축 TTFT p99, 선 두 개(1개, 3개).
3. **KV 캐시 크기**: 복제본 1개일 때와 3개일 때 각각.
4. **한 줄 결론**: 어느 쪽이 빠른지, 어느 동시 사용자 수부터 그런지.
   - 차이가 측정 오차 수준이면 그렇게 적는다.
   - 이유를 추측해서 쓰지 않는다. 확인한 것(KV 캐시 크기, GPU 사용률 등)만 쓰고, 나머지는 "원인 미확인"으로 둔다.

## 규칙

- 메모리 7 GB를 넘거나 GPU가 멈추면 즉시 중단하고 컨테이너를 내린 뒤 보고한다.
- 끝나면 컨테이너를 모두 내린다: `docker rm -f r1 r2 r3 <nginx>`.
- 맥에 보고할 것: 표, 그림 경로, 한 줄 결론, 계획과 달라진 점(메모리 값 조정 등).
