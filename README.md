# ttalkkak (딸깍)

**칩 회사가 칩과 함께 주는 AI 인프라 소프트웨어 스택.** 클라우드 사업자가 그 칩 위에서 AI 에이전트 서비스를 바로 운영하게 해 줍니다. NVIDIA가 GPU에 NIM·Dynamo를 얹어 주는 것과 같은 자리를, 다른 추론 칩 회사가 채울 때 필요한 소프트웨어를 만들어 봅니다.

> **상태 (2026-11-12): 구현은 되어 있고, 실제 GPU에서 처음부터 끝까지 돌린 적은 아직 없습니다.**
> 단위 테스트 295개는 GPU 없이 통과합니다. 아래 표의 "실기 검증" 칸이 채워져야 "된다"고 말합니다. 지금은 프로젝트 1(뼈대 실증)을 진행 중입니다.

## 누가 무엇을 하나

| 누구 | 예 | 하는 일 | 화면 |
|---|---|---|---|
| 칩 회사 | 추론 칩 회사 | ttalkkak을 칩과 함께 제공, 자기 칩과 엔진을 끼움 | (코드) |
| 클라우드 사업자 | 클라우드 회사의 운영자 | 고객·키·사용량·청구 관리, 지표 확인, 모델 올리기 | 웹 콘솔, `ttalkkak ops` |
| 클라우드 이용자 | 그 클라우드의 고객 | 터미널에서 에이전트 사용 | `ttalkkak` |

지금 "칩" 자리에는 RTX 3060 Ti(8GB)와 vLLM이 들어가 있습니다. 다른 칩을 끼우는 자리는 아직 열려 있지 않습니다(프로젝트 2에서 설계).

## 기능과 상태

| 기능 | 구현 | 실기 검증 |
|---|---|---|
| 이용자: 설치 → 브라우저 로그인 → 터미널 대화, 턴마다 토큰·예상 요금 | 됨 (도구 없는 대화) | 아직 |
| 사업자: 고객(테넌트)·API 키·분당 토큰 한도 | 됨 | 아직 |
| 사업자: 게이트웨이 배포 (Envoy AI Gateway + llm-d + vLLM, k3s) | 됨 | 아직 |
| 사업자: 전력 측정 기반 토큰당 원가와 권장 가격 | 됨 | 아직 |
| 사업자: 사용량 집계와 청구서 (추정, 결제 없음) | 됨 | 아직 |
| 사업자: 웹 콘솔 (개요·원가·테넌트·사용량) | 됨 | 아직 |
| 사업자: 지표 대시보드와 경보 (Prometheus, Grafana) | 됨 | 아직 |
| 사업자: 새 모델을 올려도 되는지 확인 (설정 적용 여부, 정확도, tool calling) | 됨 | 아직 |
| 몰려도 버티기 (복제본, 라우팅, 실시간 지표) | 일부 (복제본 수, llm-d 라우팅) | 아직 |
| 작은 모델 증폭 (여러 번 풀고 고르기) | 안 됨 | - |
| 도구와 스킬을 쓰는 터미널 에이전트 | 안 됨 (스킬 구조만 있음) | - |
| 무중단 모델 교체, 유휴 시 축소 | 안 됨 | - |

만드는 순서와 이유는 [제품 정의서](docs/specs/2026-11-12-ttalkkak-product-definition.md)에 있습니다.

## 설치

```bash
uv tool install ttalkkak                    # 이용자: 터미널 에이전트 (ttalkkak)
uv tool install "ttalkkak[ops,measure]"     # 사업자: ttalkkak ops ...
```

## 이용자

```bash
ttalkkak login --endpoint https://<게이트웨이 주소>   # 브라우저 로그인 (OIDC 디바이스 흐름)
ttalkkak                                              # 대화. 턴마다 "입력 120 (캐시 64) · 출력 45 · 약 0.03원"
ttalkkak whoami
ttalkkak logout
```

## 사업자

요구사항: NVIDIA GPU, Docker, k3s(또는 다른 쿠버네티스), Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
# 1. 모델을 올려도 되는지 확인하고 설정을 고른다
ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct

# 2. 전력을 재며 부하를 걸어 원가와 권장 가격을 낸다
ttalkkak ops cost --run <run>

# 3. 고객과 키, 로그인 사용자
ttalkkak ops tenants add acme --name Acme
ttalkkak ops tenants key-issue acme
ttalkkak ops tenants user-add acme u1@acme

# 4. 게이트웨이 스택을 만들어 배포한다
ttalkkak ops export --target gateway --run <run> --out deploy/ --hostname <호스트>
ttalkkak ops cluster up --out deploy/
ttalkkak ops cluster status

# 5. 웹 콘솔, 청구서
ttalkkak ops ui
ttalkkak ops bill --from-cluster --from 2026-11-01 --to 2026-12-01 --cost runs/<run>/cost.json
```

`ops export`는 `k8s`, `kserve`, `llmd`, `dynamo` 대상도 지원합니다. 요금은 추정이고, 실제 결제는 하지 않습니다.

### 새 모델을 올려도 되는지 확인 (`ttalkkak ops up`)

서빙이 "에러 없이 떴다"와 "에이전트가 그대로 써도 된다"는 다릅니다. 준 설정이 조용히 무시되거나, fp8 KV 캐시로 정확도가 무너졌는데 응답은 그럴듯하거나, reasoning 파서가 `<think>`를 본문으로 흘리는 경우가 그렇습니다. `ops up`은 후보 설정마다 엔진을 띄운 뒤 다음을 확인하고, 통과한 것 중 가장 빠른 설정을 고릅니다.

- **applied**: 시작 로그와 `/metrics`에서 실제로 적용된 값을 읽어 요청과 대조
- **accuracy**: gsm8k 100문항, 기준 대비 하락 5%p 이하
- **agent**: tool call 파싱, `<think>` 누출, 스트리밍과 비스트리밍 일치, JSON schema 준수
- **perf**: guidellm 부하

결과는 `runs/<id>/`에 단계별 JSON과 `report.md`로 남습니다. 엔진은 vLLM과 SGLang, 하드웨어는 NVIDIA와 AMD(ROCm)를 코드로 지원합니다.

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

## 정직하게 적는 한계

- **실제 GPU에서 처음부터 끝까지 돌린 적이 없습니다.** 엔진 로그 형식, 게이트웨이 CRD 필드, 지표 이름은 소스와 문서로 확인했고 실행으로는 확인하지 않았습니다. 확인 항목은 [docs/desktop/stage5-desktop.md](docs/desktop/stage5-desktop.md)에 있습니다.
- **분산은 GPU 한 장 위의 복제본 여러 개입니다.** 기계 여러 대가 아닙니다.
- **다른 칩은 실물이 없습니다.** 실제로 끼워 본 것은 NVIDIA GPU뿐입니다.
- 잘못된 HF 모델 ID나 `config.json`이 없는 저장소는 아직 traceback으로 끝납니다.
- 저장소 폴더에서 실행해야 기본 skill을 찾습니다.

## 라이선스

[Apache License 2.0](LICENSE)
