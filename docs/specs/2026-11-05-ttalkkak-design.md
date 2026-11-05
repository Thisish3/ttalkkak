# ttalkkak 설계 (1단계 MVP)

- 작성일: 2026-11-05
- 상태: 사용자 리뷰 대기
- 목적: 취업 포트폴리오. 2~4주 안에 데모 가능한 상태.

## 1. 한 줄 정의

HF 모델(또는 NIM API)과 skill 몇 개를 고르면, **skill이 요구하는 조건에 맞춰 서빙 설정을 정하고, 실제로 띄워서 설정이 적용됐는지·정확도가 맞는지·에이전트가 쓸 수 있는지 검증한 뒤**, LangGraph 에이전트를 그 엔드포인트에 바로 연결하는 CLI.

```
ttalkkak up --model Qwen/Qwen2.5-7B-Instruct-AWQ --skills web-search,code-exec
```

## 2. 왜 만드나 (차별점)

기존 도구 조사(2026-11-05) 결과, "GUI로 자동 배포"는 이미 강자가 있다. ttalkkak은 그 앞뒤의 빈 곳을 맡는다.

| 도구 | 하는 일 | ttalkkak과의 관계 |
|---|---|---|
| GPUStack (★≈6.0k) | GPU 클러스터 관리, vLLM/SGLang 자동 배포, sweep 벤치마크 | GUI 배포는 겹침. 개요 문서에 정확도 검증 언급 없음. 적용 검증 기능 유무는 미확인 |
| llm-optimizer (BentoML, ★≈200) | vLLM/SGLang 인자 grid 실측 + SLO 필터 + 이론 추정 | 비교 대상. README GPU 목록은 H100/H200/A100/L20/L40/B100/B200 뿐 |
| AIConfigurator (ai-dynamo, ★≈460) | CPU 성능 모델로 설정 예측, Dynamo/llm-d 배포 설정 생성 | 3단계에서 분산 구성 결정을 위임할 후보 |
| vLLM 자체 | `max_model_len=-1` 자동 맞춤, `benchmarks/auto_tune` | 엔진 내부 최적화. 무시된 설정을 알려 주지는 않음 |

ttalkkak만의 것:

1. **적용 검증(applied)**: 요청한 설정과 엔진이 실제로 쓴 값을 대조한다. (예: vLLM #56021, RDNA에서 환경변수가 조용히 무시됨)
2. **정확도 smoke**: 빠르지만 틀린 설정을 거른다. (예: fp8 KV가 그럴듯하지만 틀린 응답을 냄)
3. **에이전트 적합성(agent)**: tool/reasoning 파서, 스트리밍과 비스트리밍 일치, `parallel_tool_calls`. (예: sglang 파서 버그, Dynamo `<think>` 누출 frontend-crates #477, Dynamo #16399)
4. **skill 요구사항 → 서빙 설정**: 에이전트가 무엇을 하려는지가 서빙 설정을 결정한다.
5. **예측 vs 실측 리포트**: 규칙이 왜 그 후보를 골랐고, 실측이 그것을 뒤집었는지 남긴다.
6. **소비자 GPU(8GB)부터 MI300X까지**: 기존 도구가 다루지 않는 하드웨어 양 끝.

## 3. 범위와 단계

| 단계 | 내용 | 끝났을 때 데모 |
|---|---|---|
| **1 (이 문서)** | 3060 Ti 8GB, vLLM, HF 모델 또는 NIM API, skill 2~3개, CLI. 전체 흐름이 끝까지 돈다 | `ttalkkak up` 한 줄 → 리포트 + 동작하는 에이전트 |
| 2 | SGLang 어댑터, AMD Developer Cloud MI300X | 같은 입력에 하드웨어마다 다른 결정 |
| 3 | K8s: 결정을 llm-d Helm values / Dynamo DGD / KServe LLMInferenceService로 출력(단위 테스트), 하나만 실배포. 분산 구성은 AIConfigurator 연동 검토 | 매니페스트 생성 + 실배포 1건 |
| 4 | GUI (HF 모델 검색 → 실행 → 리포트 화면). 단계별 JSON만 읽는 얇은 층 | 딸깍딸깍 |

1단계에서 하지 않는 것: SGLang, ROCm, K8s, GUI, 여러 GPU, LoRA, 가드레일, 관측(Langfuse/Phoenix), 양자화 수행.

## 4. 구조

```
[1] 구성       ttalkkak.yaml (또는 CLI 인자)
               model: HF ID | nim:<model>   skills: [...]
[2] 요구 합치기  skills/*/ttalkkak.yaml → Requirements
[3] 서빙        probe → plan → launch → verify → pick
[4] 에이전트     LangGraph + 선택된 skills + 엔드포인트
[5] report     Markdown + JSON
```

### 4.1 단계별 입출력

모든 단계는 JSON 하나를 입력받아 JSON 하나를 쓴다. 작업 디렉터리 `runs/<timestamp>/`에 저장한다. 단계별 단독 실행(`ttalkkak plan --from probe.json`)이 가능해야 한다. GUI(4단계)는 이 파일들만 읽는다.

| 단계 | 입력 | 출력 | 내용 |
|---|---|---|---|
| probe | 모델 ID, 장비 | `probe.json` | GPU 이름/메모리/compute capability(`nvidia-smi`), HF `config.json`(레이어 수, KV 헤드, head_dim, dtype, 양자화 설정, 최대 위치), chat template 유무, 가중치 크기(HF API 파일 목록) |
| plan | probe, Requirements | `plan.json` | 후보 2~3개. 각 후보 = 엔진 인자 + 예측값(가중치·KV 메모리, 가능한 max_model_len) + 선택 이유 문장 |
| launch | plan | `launch.json` | 후보별 컨테이너 기동, 준비 대기, 실패 분류 |
| verify | launch | `verify.json` | applied / accuracy / agent / perf 4종 |
| pick | verify | `pick.json` | 통과 후보 중 선택, 나머지 컨테이너 종료 |
| report | 전부 | `report.md` | 예측 vs 실측 표, 탈락 사유, LangGraph 연결 설정 |

NIM API 모델(`nim:` 접두사)은 probe/plan/launch를 건너뛰고 verify(accuracy, agent)부터 실행한다.

### 4.2 엔진 어댑터

```python
class EngineAdapter(Protocol):
    name: str
    def build_args(self, candidate: Candidate) -> ContainerSpec: ...
    def read_applied(self, logs: str, metrics: str) -> AppliedConfig: ...
    def health(self, base_url: str) -> bool: ...
```

엔진별 지식(플래그 이름, 로그 형식, 이미지 태그)은 어댑터 안에만 둔다. 1단계는 `VllmAdapter` 하나.

`read_applied`가 읽어야 하는 최소 항목: 실제 `max_model_len`, KV cache dtype, KV 캐시 블록 수(또는 토큰 수), attention backend, tool/reasoning parser, 모델 dtype/양자화 방식. 각 값에 근거(로그 줄 또는 metrics 이름)를 같이 남긴다. 못 읽은 값은 `unknown`으로 둔다.

### 4.3 실행 환경

Docker 컨테이너(공식 `vllm/vllm-openai` 이미지, 버전 고정). 3060 Ti 데스크탑(Windows + Docker, 2026-10에 vLLM 구동 확인됨)에서 실행한다. ttalkkak 자체는 Python 3.12 + uv 패키지.

## 5. skill

### 5.1 형식

Agent Skills 표준(agentskills.io)을 따르는 폴더에 ttalkkak 전용 파일을 옆에 둔다. 표준 `SKILL.md`는 수정하지 않는다.

```
skills/web-search/
├── SKILL.md          # 표준: name, description frontmatter + 지침 본문
├── ttalkkak.yaml     # ttalkkak 확장 (스키마 검증)
├── scripts/  references/   # 표준 선택 폴더
```

```yaml
# skills/web-search/ttalkkak.yaml
requires:
  tool_calling: true
  streaming: true
min_context: 16384
mcp_servers:
  - name: fetch
    command: ["uvx", "mcp-server-fetch"]
```

### 5.2 로더

직접 구현한다(작음: frontmatter 파싱, 본문을 시스템 프롬프트에 주입, `ttalkkak.yaml` pydantic 검증). deepagents `SkillsMiddleware` 호환은 나중에 선택 기능으로 둔다. MCP 서버 연결은 `langchain[mcp]`(`langchain.mcp`)를 쓴다. `langchain-mcp-adapters`는 2026-09에 archived되어 쓰지 않는다.

### 5.3 1단계 기본 skill

| skill | requires | MCP |
|---|---|---|
| `web-search` | tool_calling, streaming, min_context 16k | fetch 계열 공식 서버 |
| `code-exec` | tool_calling, structured_output | 로컬 샌드박스 실행 서버(선정 필요) |
| `file-qa` | min_context 32k | filesystem 공식 서버 |

## 6. 요구사항 → 서빙 설정

### 6.1 합치기

선택된 skill들의 `requires`는 합집합(OR), `min_context`는 최댓값. 결과를 `Requirements`로 만들고, 각 항목에 "어느 skill 때문인지"를 기록한다.

### 6.2 매핑

| 요구사항 | plan이 정하는 것 | verify.agent가 확인하는 것 |
|---|---|---|
| tool_calling | `--enable-auto-tool-choice --tool-call-parser <p>`. `<p>`는 ttalkkak의 모델 계열→파서 표에서 | 고정 tool call 프롬프트에서 함수 이름·인자 파싱 |
| reasoning | `--reasoning-parser <p>` | reasoning 내용이 content로 새지 않음 |
| streaming | (기본) | 같은 요청의 스트리밍/비스트리밍 결과(텍스트, tool call) 일치 |
| parallel_tool_calls=false | (요청 옵션) | call이 정확히 1개 |
| min_context N | `--max-model-len ≥ N` | 실제 적용된 max_model_len ≥ N |
| structured_output | guided decoding 기본 백엔드 | JSON schema 준수 |

### 6.3 모델 계열 → 파서 표

`ttalkkak/data/parsers.yaml`에 수동으로 관리한다. 표가 후보를 내고 verify.agent가 실측으로 확인한다. 표와 실측이 어긋나면 리포트에 남긴다. 자동 추론(chat template, 엔진 파서 레지스트리)은 1단계에서 하지 않는다.

### 6.4 메모리가 부족할 때

8GB에서 가중치 + KV(`min_context`)가 들어가지 않으면 plan은 다음 순서로 후보를 바꾼다.

1. KV dtype fp8. 이 후보는 accuracy smoke를 통과해야만 선택될 수 있다.
2. 같은 모델의 양자화본(HF에서 AWQ/GPTQ 리포 검색).
3. 같은 계열의 더 작은 모델.

모두 불가능하면 원인이 된 요구사항과 skill을 지목해 실패로 보고한다(예: "file-qa의 min_context 32k 때문에 이 GPU에서 불가").

메모리 계산식은 KV 바이트 = 2 × 레이어 수 × KV 헤드 수 × head_dim × dtype 바이트 × 토큰 수. 계산값은 예측일 뿐이며, 실제 KV 용량은 엔진 로그에서 읽어 리포트에서 대조한다.

## 7. 검증(verify) 상세

| 검사 | 방법 | 통과 기준(1단계 초기값, 실측 후 조정) |
|---|---|---|
| applied | `read_applied` 결과와 후보 인자 비교 | 의도한 항목 전부 일치. 불일치는 경고(선택은 가능) |
| accuracy | lm-evaluation-harness 작은 태스크(예: gsm8k 일부 100문항, 고정 seed). 같은 모델의 기준 설정(bf16 KV) 점수와 비교 | 기준 대비 정답률 하락 5%p 이하(초기값). 100문항이라 표본 오차가 크므로, 첫 e2e 실측에서 기준 설정을 3회 반복해 편차를 재고 이 값을 갱신한다 |
| agent | ttalkkak 자체 고정 세트(tool call, reasoning 누출, 스트림 일치, 단일 call) | 전부 통과 |
| perf | guidellm(짧은 synchronous + 동시성 1개 구간) | 선택 기준으로만 사용, 탈락 기준 아님 |

선택 규칙: accuracy와 agent를 통과한 후보 중 perf가 가장 좋은 것. 동률이면 메모리 여유가 큰 것.

## 8. 에이전트 실행

LangGraph 기본 ReAct 루프 템플릿. 선택된 엔드포인트(OpenAI 호환)와 skill 지침, MCP 도구를 연결한다. `ttalkkak up`이 끝나면 다음 두 가지를 출력한다.

- `ttalkkak chat`: 바로 대화하는 TUI
- 다른 코드에서 쓸 수 있는 연결 설정(엔드포인트, 모델 이름, 파서 관련 요청 옵션)

## 9. 오류 처리

| 상황 | 처리 |
|---|---|
| 후보 기동 실패 | 해당 후보만 `failed`. 원인 분류(oom / bad_arg / download / timeout / unknown) + 로그 마지막 50줄. 다음 후보로 진행 |
| applied 불일치 | `mismatch` 경고. "요청 X, 실제 Y, 근거" 기록. 리포트 상단에 표시 |
| accuracy / agent 실패 | 선택 대상에서 제외. "빠르지만 탈락" 목록에 기록 |
| 전 후보 실패 | 종료 코드 1, 원인 요구사항·skill 보고 |
| 중단(Ctrl-C) | 기동한 컨테이너 정리, 그때까지의 JSON 저장. `--resume <run>`으로 이어 하기 |

모든 외부 호출(HF API, 컨테이너 기동, 측정)에 타임아웃을 둔다. 원인을 판정하지 못하면 추측하지 않고 `unknown`과 원본 로그를 남긴다.

## 10. 테스트

| 층 | 방법 | GPU |
|---|---|---|
| plan 규칙 | 실제 모델 `config.json` 몇 개를 fixture로 고정, 메모리 계산과 후보 결과 스냅샷 테스트 | 불필요 |
| `read_applied` | 실제 vLLM 시작 로그와 `/metrics`를 녹화한 fixture로 파싱 테스트. vLLM 버전 변경 시 fixture 추가 | 불필요 |
| verify.agent | mock OpenAI 서버가 정상·불량 응답(`<think>` 누출, 스트림 불일치, call 2개, 잘못된 JSON)을 재생. 검사기가 각각을 잡는지 확인 | 불필요 |
| skill 로더·요구사항 합치기 | 단위 테스트 | 불필요 |
| end-to-end | 3060 Ti에서 작은 모델(Qwen2.5-0.5B/1.5B-Instruct 등)로 `ttalkkak up` 전체 실행. 결과 run 디렉터리를 커밋 | 필요 |

GitHub Actions에서는 GPU 불필요 항목만 실행한다.

## 11. 미리 확인할 것 (구현 계획 전)

1. Agent Skills 표준 원문(agentskills.io)의 필수·선택 필드. `SKILL.md` 파싱 범위 확정.
2. `langchain[mcp]`의 현재 API(`langchain.mcp`)와 LangGraph 연결 예제.
3. 고정할 vLLM 이미지 버전에서 `read_applied` 대상 값이 어떤 로그 줄/metrics로 나오는지.
4. NIM 호스팅 API 무료 체험의 현재 조건(크레딧, rate limit). 사용자 가입 필요.
5. `code-exec`용 샌드박스 MCP 서버 선정.

## 12. 참고

- 대체한 프로젝트: mini-nvidia-stack(보관). NeMo Agent Toolkit은 2026-10 deprecated(후속: NeMo Relay / Helix / Fabric)라 기반으로 쓰지 않는다.
- 2026-11-05 확인한 최신 릴리스: llm-d v1.0.0(2026-10-20), Dynamo v1.6.1(2026-10-27), KServe v0.21.0(2026-09-25), NeMo Guardrails v0.24.1(2026-09-16).
