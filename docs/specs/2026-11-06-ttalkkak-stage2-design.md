# ttalkkak 2단계 설계: SGLang과 AMD MI300X

- 작성일: 2026-11-06
- 상태: 사용자 리뷰 대기
- 선행: 1단계(`docs/specs/2026-11-05-ttalkkak-design.md`). 1단계 GPU 실측(Task 15)은 아직이며, 이 단계도 fixture로 먼저 검증하고 마지막 작업에서 실측으로 교체한다.

## 1. 목표

같은 `ttalkkak up` 입력에서 **vLLM과 SGLang을 모두 후보로 돌려 실측으로 고르고**, NVIDIA(RTX 3060 Ti)와 AMD(MI300X, AMD Developer Cloud) 양쪽에서 같은 흐름이 돈다. 리포트에는 "같은 모델인데 엔진·하드웨어마다 결정이 이렇게 다르다"가 남는다.

## 2. 결정 사항 (사용자 합의)

| 항목 | 결정 |
|---|---|
| 엔진 선택 | 두 엔진 모두 후보로 돌리고 1단계와 같은 검증·선택 규칙으로 고른다 |
| 후보 구성 | c1 vLLM KV auto, c2 vLLM KV fp8, c3 SGLang 1개 (auto가 필요한 문맥을 담으면 auto, 아니면 fp8). 기준선 전용 c0 규칙은 1단계 그대로(vLLM) |
| 실행 순서 | 1단계처럼 한 번에 하나씩 띄우고 검증 후 내린다 |

## 3. 확인한 사실 (2026-11-06)

- SGLang 서버 인자는 필드 이름을 하이픈으로 바꾼 CLI다(`python/sglang/srt/arg_groups/arg_utils.py` `_field_to_cli_name`). 사용하는 것: `--model-path`, `--host`, `--port`, `--context-length`, `--mem-fraction-static`, `--kv-cache-dtype`(선택지에 `auto`, `fp8_e5m2`, `fp8_e4m3` 포함), `--tool-call-parser`, `--reasoning-parser`, `--enable-metrics`.
- SGLang `/server_info`는 해석이 끝난 서버 인자와 `launch_command`, `internal_states`를 돌려준다(`entrypoints/http_server.py`). `/health`, `/v1/models`도 있다.
- SGLang 시작 로그 예(문서): `max_total_num_tokens=665690, chunked_prefill_size=8192, ..., context_len=65536, available_gpu_mem=13.50 GB`.
- SGLang 파서 이름: tool `qwen25`(Qwen2.5), `llama3`, `mistral`, `hermes`; reasoning `qwen3`, `deepseek-r1`(`function_call/function_call_parser.py`, `parser/reasoning_parser.py`).
- 이미지: `lmsysorg/sglang:v0.5.23`(CUDA), `lmsysorg/sglang:v0.5.23-rocm720-mi30x`(MI300X). vLLM ROCm은 `vllm/vllm-openai-rocm:v0.33.0`(Docker Hub에 태그 존재 확인).
- AMD 컨테이너는 `--device=/dev/kfd --device=/dev/dri`와 `video`, `render` 그룹이 필요하다(SGLang AMD 문서).

## 4. 구조

```
probe ── nvidia-smi 또는 amd-smi → GpuInfo(vendor, arch 추가)
plan  ── c1 vLLM auto · c2 vLLM fp8 · c3 SGLang (엔진 중립 인자)
launch ─ Platform(vendor) → 이미지, GPU 플래그 / EngineAdapter → 엔진 플래그
verify · pick · report ── 엔진 무관 (1단계 그대로)
```

### 4.1 엔진 중립 후보 인자

`Candidate.args`는 엔진과 무관한 이름만 쓴다: `max_model_len`, `kv_cache_dtype`(`auto` | `fp8`), `gpu_memory_utilization`, `tool_call_parser`, `reasoning_parser`, `enable_auto_tool_choice`. `Candidate.engine`은 `vllm | sglang`.

어댑터가 엔진 플래그로 바꾼다.

| 중립 인자 | vLLM | SGLang |
|---|---|---|
| `max_model_len` | `--max-model-len` | `--context-length` |
| `kv_cache_dtype=fp8` | `--kv-cache-dtype fp8` | `--kv-cache-dtype fp8_e4m3` |
| `gpu_memory_utilization` | `--gpu-memory-utilization` | `--mem-fraction-static` |
| `tool_call_parser` | `--tool-call-parser` + `--enable-auto-tool-choice` | `--tool-call-parser` |
| `reasoning_parser` | `--reasoning-parser` | `--reasoning-parser` |
| 모델 | `--model` | `--model-path` |
| 메트릭 | (기본) | `--enable-metrics` |

### 4.2 어댑터 인터페이스 확장

```python
class EngineAdapter(Protocol):
    name: str
    container_port: int
    def build_args(self, c: Candidate) -> ContainerSpec: ...
    def collect(self, base_url: str, client: httpx.Client) -> dict[str, object]: ...
    def read_applied(self, logs: str, collected: dict[str, object]) -> AppliedConfig: ...
    def health(self, base_url: str, client: httpx.Client) -> bool: ...
```

- `collect`: 엔진별로 필요한 엔드포인트를 모은다. vLLM은 `/v1/models`(JSON)와 `/metrics`(텍스트), SGLang은 `/server_info`(JSON). 실패한 엔드포인트는 빠진 채로 둔다.
- `read_applied`가 돌려주는 값은 **중립 이름과 중립 값**이다(예: SGLang의 `fp8_e4m3` → `fp8`). 그래서 `compare_applied`는 엔진을 모른다.
- `Launched.metrics`, `Launched.models_json`은 `Launched.collected: dict`로 합친다.

### 4.3 SGLang 적용값

| 적용값 | 1순위 | 2순위 |
|---|---|---|
| `max_model_len` | `/server_info` `context_length` | 로그 `context_len=` |
| `kv_cache_dtype` | `/server_info` `kv_cache_dtype` | — |
| `kv_cache_tokens` | `/server_info` 최상위 `max_total_num_tokens`, 없으면 `internal_states[0].memory_usage.token_capacity` | 로그 `max_total_num_tokens=` |
| `tool_call_parser`, `reasoning_parser` | `/server_info` | — |
| `attention_backend` | `/server_info` `attention_backend` | — |

값마다 근거(엔드포인트 필드 이름 또는 로그 줄)를 남기고, 못 읽으면 `unknown`.

### 4.4 Platform

```python
class Platform(BaseModel):
    vendor: Literal["nvidia", "amd"]
    def image(self, engine: str) -> str: ...
    def gpu_flags(self) -> list[str]: ...
```

| vendor | GPU 플래그 | vLLM 이미지 | SGLang 이미지 |
|---|---|---|---|
| nvidia | `--gpus all` | `vllm/vllm-openai:v0.33.0` | `lmsysorg/sglang:v0.5.23` |
| amd | `--device=/dev/kfd --device=/dev/dri --group-add video --group-add render --security-opt seccomp=unconfined` | `vllm/vllm-openai-rocm:v0.33.0` | `lmsysorg/sglang:v0.5.23-rocm720-mi30x` |

`--security-opt seccomp=unconfined`는 ROCm 컨테이너 안내에서 흔히 쓰이지만 필요 여부는 MI300X 실측에서 확인하고, 필요 없으면 뺀다.

### 4.5 probe

- `nvidia-smi`가 있으면 1단계 그대로 `vendor="nvidia"`.
- 없으면 `amd-smi`로 이름, VRAM, gfx 아키텍처를 읽어 `vendor="amd"`. 출력 형식은 실측 전이라 SYNTHETIC fixture로 시작한다.
- 둘 다 없으면 `gpu=None`(1단계와 같은 오류).

### 4.6 파서 표

`parsers.yaml`을 엔진별로 나눈다.

```yaml
qwen2:
  vllm: {tool: hermes, reasoning: null}
  sglang: {tool: qwen25, reasoning: null}
qwen3:
  vllm: {tool: hermes, reasoning: qwen3}
  sglang: {tool: qwen25, reasoning: qwen3}
llama:
  vllm: {tool: llama3_json, reasoning: null}
  sglang: {tool: llama3, reasoning: null}
mistral:
  vllm: {tool: mistral, reasoning: null}
  sglang: {tool: mistral, reasoning: null}
```

어느 엔진에 파서가 없으면 그 엔진 후보에만 blocker가 붙는다(원인 skill 이름 포함, 1단계 규칙).

### 4.7 SGLang 후보의 KV 선택

c3는 `--mem-fraction-static`을 지정하지 않고 SGLang 기본값에 맡긴다(작은 GPU에서 SGLang이 activation·CUDA graph 여유를 직접 잡기 때문). 그래서 c3의 KV 예측은 근사이며 리포트에 그렇게 적는다. c3는 1단계 메모리 계산으로 auto KV가 `min_context`를 담으면 `auto`, 아니면 `fp8`(어댑터가 `fp8_e4m3`로 변환). fp8이면 `requires_accuracy=True`로 기준선이 필요하다. 기준선은 vLLM c1 또는 c0의 점수를 쓴다(같은 모델·같은 평가 세트이므로 엔진이 달라도 비교한다). 이 가정은 리포트에 적는다.

## 5. 리포트 변경

- 예측 vs 실측 표에 `엔진` 열을 추가한다.
- 1단계 Minor 중 "동률이면 c1"은 이번에 스펙대로 고친다: 동률이면 실측 `kv_cache_tokens`가 큰 후보.

## 6. 오류 처리

1단계 규칙을 그대로 따른다. 추가 사항:

- 이미지가 vendor에 없으면(예: AMD용 태그 없음) 그 엔진 후보는 계획 단계에서 blocker(`no image for <engine> on <vendor>`).
- SGLang `/server_info`가 실패하면 로그 기반 값으로 대체하고, 그래도 없으면 `unknown`.

## 7. 테스트

| 대상 | 방법 |
|---|---|
| 중립 인자 → 엔진 플래그 | 4.1 표를 그대로 단위 테스트 |
| SGLang `read_applied` | `server_info` JSON fixture(SYNTHETIC, 소스 필드 이름 기준) + 시작 로그 fixture. `/server_info` 없을 때 로그 대체 |
| normalize | `fp8_e4m3` → `fp8`이어서 `compare_applied`가 일치로 본다 |
| Platform | vendor별 이미지·플래그, AMD에서 `--gpus`가 붙지 않음 |
| probe AMD | `amd-smi` 출력 fixture(SYNTHETIC) 파싱 |
| 파서 표 | 엔진별 조회, 한 엔진만 없을 때 그 후보만 blocker |
| pipeline | c1, c2, c3가 한 번에 하나씩 뜨고 엔진이 다른 후보가 선택될 수 있음 |

실측 작업(마지막, 사람이 실행):

1. 3060 Ti: SGLang 시작 로그와 `/server_info` 실물로 fixture 교체.
2. MI300X: `amd-smi` 출력, ROCm 이미지 2개로 `ttalkkak up` 전체 실행, seccomp 플래그 필요 여부 확인.

## 8. 범위 밖

TensorRT-LLM, 여러 GPU(TP), LoRA, SGLang 고유 튜닝(chunked prefill 크기, radix cache 설정 비교).
