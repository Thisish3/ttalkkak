# ttalkkak (딸깍)

**Agent-ready LLM serving.** Pick a model and the skills your agent needs; ttalkkak picks a vLLM configuration for them, launches it, checks that the settings were actually applied and that accuracy and tool calling still work, and hands a LangGraph agent the endpoint.

> **상태: 1~4단계 구현 완료, GPU 실측 전.** 단위 테스트 175개는 GPU 없이 통과하지만, 실제 GPU(RTX 3060 Ti, AMD MI300X)에서 엔진을 띄우는 end-to-end와 실제 클러스터 적용은 아직 하지 않았습니다. `tests/fixtures/` 중 `SYNTHETIC` 표시가 있는 파일은 그때 실측으로 교체합니다.

## 왜 만드나

서빙이 "에러 없이 떴다"와 "에이전트가 그대로 써도 된다"는 다릅니다. 직접 겪은 사례는 모두 서버가 정상처럼 보이는 상태에서 틀렸습니다.

- 준 설정이 조용히 무시됨 (vLLM에서 RDNA일 때 AITER unified attention 환경변수)
- fp8 KV 캐시로 정확도가 무너졌는데 응답은 그럴듯함
- reasoning 파서가 `<think>`를 content로 흘림, `parallel_tool_calls=false`에서 스트리밍과 비스트리밍이 다른 tool call을 고름
- usage가 없는 서버에서 벤치마크 도구가 처리량을 3배로 보고

ttalkkak은 이런 상태를 **띄운 뒤에 확인**합니다. 자세한 배경과 기존 도구(GPUStack, llm-optimizer, AIConfigurator)와의 차이는 [설계 문서](docs/specs/2026-11-05-ttalkkak-design.md) 2절에 있습니다.

## 동작 방식

```
ttalkkak ops up --model Qwen/Qwen2.5-7B-Instruct-AWQ --skills web-fetch,code-exec
```

1. **skill 요구사항 합치기**: 각 skill의 `ttalkkak.yaml`(tool calling, reasoning, streaming, structured output, 최소 문맥 등)을 합치고, 항목마다 어느 skill 때문인지 기록합니다.
2. **probe**: HF `config.json`과 `nvidia-smi`로 레이어 수, KV 헤드, 가중치 크기, GPU 메모리를 읽습니다.
3. **plan**: KV 캐시 메모리를 계산해 후보를 만듭니다. KV `auto`(c1)와 `fp8`(c2)이고, 모델 계열에 맞는 tool/reasoning 파서를 붙입니다. auto로 필요한 문맥이 안 들어가면 정확도 기준선 전용 후보(c0)를 추가합니다.
4. **launch → verify → stop (후보 하나씩)**: vLLM을 Docker로 띄우고 다음을 확인한 뒤 내립니다.
   - **applied**: 시작 로그(`non-default args`, `Using max model len`, `GPU KV cache size`), `/metrics`의 `vllm:cache_config_info`, `/v1/models`에서 실제로 적용된 값을 읽어 요청과 대조
   - **accuracy**: lm-evaluation-harness gsm8k 100문항, 기준(KV auto) 대비 하락 5%p 이하
   - **agent**: tool call 파싱, `<think>` 누출, 스트리밍/비스트리밍 일치, `parallel_tool_calls=false`에서 call 1개, JSON schema 준수
   - **perf**: guidellm synchronous 20요청
5. **pick**: 정확도와 에이전트 검사를 통과한 후보 중 출력 tok/s가 가장 높은 것을 다시 띄워 둡니다.
6. **report**: `runs/<id>/report.md`에 경고(설정 불일치), 예측 vs 실측 표, 탈락 사유, LangGraph 연결 코드를 남깁니다.

단계마다 결과가 `runs/<id>/{probe,plan,launch,verify,pick}.json`으로 저장되고, `--resume <id>`로 probe·plan을 건너뛰고 이어서 실행할 수 있습니다.

## 설치

```bash
uv tool install ttalkkak                    # 사용자: 콘솔 에이전트 (ttalkkak)
uv tool install "ttalkkak[ops,measure]"     # 운영자: ttalkkak ops ...
```

## 시작하기

요구사항: NVIDIA GPU, Docker(`--gpus all` 사용 가능), Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Thisish3/ttalkkak.git
cd ttalkkak
uv sync --all-extras
export HF_TOKEN=...        # gated 모델이면 필요, 컨테이너에 그대로 전달됩니다

uv run ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct --skills code-exec
uv run ttalkkak ops chat --run <runs 아래 id> --skills code-exec
```

엔진은 vLLM(c1 KV auto, c2 KV fp8)과 SGLang(c3)을 모두 후보로 돌리고, NVIDIA는 `nvidia-smi`, AMD는 `amd-smi`로 감지해 이미지와 GPU 플래그를 바꿉니다.

### 배포 매니페스트로 내보내기

```bash
uv run ttalkkak ops export --run <id> --target k8s     --out deploy/   # Deployment + Service
uv run ttalkkak ops export --run <id> --target kserve  --out deploy/   # LLMInferenceService (vLLM)
uv run ttalkkak ops export --run <id> --target llmd    --out deploy/   # llm-d v1.0.0 kustomize overlay
uv run ttalkkak ops export --run <id> --target dynamo  --out deploy/ --image <dynamo runtime image>
```

검증을 통과한 후보의 인자와 이미지를 그대로 씁니다. 각 디렉터리의 `README.md`에 Secret 생성과 적용 명령이 있습니다.

### 웹 화면

```bash
uv run ttalkkak ops ui        # http://127.0.0.1:8787
```

HF 모델 검색(가중치 기준으로 이 GPU에 들어가는지 표시), skill 선택, 실행과 단계 진행, 리포트, 내보내기를 브라우저에서 합니다. 기본으로 127.0.0.1에만 열고, 다른 Host 헤더와 다른 출처의 POST는 거부합니다.

종료 코드: `0` 선택 성공, `1` 모든 후보 탈락, `2` 입력·모델 접근 오류, `3` Docker 사용 불가.

NVIDIA NIM 호스팅 모델은 `--model nim:<model>`로 고르고 `NVIDIA_API_KEY`를 설정합니다. 이 경우 로컬 서빙 없이 에이전트 적합성 검사만 실행합니다(정확도 검사는 아직 제외).

## skill

skill은 [Agent Skills 표준](https://agentskills.io/specification) 폴더입니다. 표준 `SKILL.md`는 그대로 두고, ttalkkak 전용 요구사항은 옆의 `ttalkkak.yaml`에 둡니다. 그래서 같은 skill을 다른 harness에서도 읽을 수 있습니다.

```
skills/file-qa/
├── SKILL.md        # name, description frontmatter + 지침
└── ttalkkak.yaml   # ttalkkak 확장
```

```yaml
requires: {tool_calling: true}
min_context: 32768
mcp_servers:
  - {name: files, command: [uv, run, python, -m, ttalkkak.mcp_servers.files]}
```

기본 skill:

| skill | 하는 일 | MCP 서버 |
|---|---|---|
| `web-fetch` | URL을 가져와 출처와 함께 답함 | `mcp-server-fetch` (uvx) |
| `code-exec` | 짧은 Python을 격리된 컨테이너에서 실행 | 내장 `pyexec` (네트워크 없음, read-only, 권한 제거, 비root) |
| `file-qa` | 폴더 안 텍스트 파일에 대해 답함 | 내장 `files` (`TTALKKAK_FILES_ROOT` 밖 접근 거부) |

MCP 도구는 `langchain.mcp`(베타)로 연결합니다.

## 개발

```bash
uv run pytest -q
uv run ruff check .
```

GPU가 필요한 부분은 모두 주입 가능한 함수 뒤에 있고, 테스트는 가짜 Docker와 mock OpenAI 서버로 돌아갑니다. mock 서버는 정상 응답뿐 아니라 `<think>` 누출, 스트림 불일치, call 2개, 여러 청크로 쪼개진 인자처럼 일부러 망가뜨린 응답도 재생해서, 검사기가 그것을 잡아내는지 확인합니다.

- 단계별 설계와 계획: [docs/specs/](docs/specs/), [docs/plans/](docs/plans/)

## 로드맵

| 단계 | 내용 |
|---|---|
| 1 (구현 완료) | RTX 3060 Ti 8GB, vLLM, HF 모델 또는 NIM API, 기본 skill 3개, CLI |
| 2 (구현 완료) | SGLang 어댑터, AMD MI300X(ROCm) |
| 3 (구현 완료) | `ttalkkak ops export`: k8s / KServe / llm-d / Dynamo 매니페스트 |
| 4 (구현 완료) | `ttalkkak ops ui`: HF 모델 검색 → 실행 → 리포트 → 내보내기 |
| 다음 | 각 단계 실측(3060 Ti, MI300X, 실제 클러스터, 브라우저) |

## 알려진 한계

- GPU end-to-end 실측 전입니다. vLLM v0.33 로그 형식, 파서 이름, lm-eval 결과 형식은 소스와 `--help`로 확인했지만 실제 실행으로는 아직 확인하지 않았습니다.
- 잘못된 HF 모델 ID 형식이나 `config.json`이 없는 저장소는 아직 친절한 오류 대신 traceback으로 끝납니다.
- 저장소 폴더에서 실행해야 기본 skill을 찾습니다.

## 라이선스

[Apache License 2.0](LICENSE)
