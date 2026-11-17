<h1 align="center">ttalkkak <sub>딸깍</sub></h1>

<p align="center"><b>추론 칩 위에 바로 올리는 LLM 서빙 운영 스택</b><br>
모델을 고르면 띄우고, 재고, 고객을 받고, 몰리면 늘립니다.</p>

<p align="center">
<img alt="python" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
<img alt="kubernetes" src="https://img.shields.io/badge/k3s-kubernetes-326CE5?logo=kubernetes&logoColor=white">
<img alt="vllm" src="https://img.shields.io/badge/engine-vLLM-30A2FF">
<img alt="llm-d" src="https://img.shields.io/badge/router-llm--d-6E40C9">
<img alt="tauri" src="https://img.shields.io/badge/app-Tauri%20v2-24C8DB?logo=tauri&logoColor=white">
<img alt="tests" src="https://img.shields.io/badge/tests-577%20passed-2EA043">
<img alt="validated" src="https://img.shields.io/badge/validated-RTX%203060%20Ti-76B900?logo=nvidia&logoColor=white">
</p>

<p align="center"><img src="docs/images/app_live.png" alt="ttalkkak 콘솔 앱 실시간 화면" width="880"></p>

## 왜 만드나

NVIDIA는 GPU와 함께 NIM과 Dynamo를 내놓습니다. 칩만 팔지 않고, 그 칩 위에서 서비스를 돌리는 소프트웨어까지 함께 건넵니다. 다른 추론 칩 회사가 같은 자리를 채우려면 무엇이 필요한지, 실제로 만들어 보면서 확인하는 프로젝트입니다.

클라우드 사업자는 ttalkkak으로 모델을 검증해 올리고, 고객별 키와 한도를 걸고, 원가와 청구를 계산하고, 몰리는 트래픽에 맞춰 복제본을 늘리고 줄입니다. 이용자는 그 위에서 터미널 에이전트를 씁니다.

<p align="center"><img src="docs/images/ttalkkak_overview.png" alt="ttalkkak 개요: 이용자·사업자·칩 회사, Envoy AI Gateway → llm-d 라우터 → vLLM 복제본, 테넌트·원가·청구·관측" width="900"></p>

## 프로젝트 2 · 몰려도 버티는 서빙

<p align="center"><img src="docs/images/project2_architecture.png" alt="프로젝트 2 구조: 운영자 앱 → 콘솔 서버(용량 측정, 자동 증감) → Envoy AI Gateway → llm-d → vLLM 복제본" width="900"></p>

- **용량을 숫자로.** guidellm의 goodput 탐색으로 "요청 95 %가 첫 글자 1초 안"을 지키는 동시 사용자 수를 찾고, 그 속도의 열린 부하로 한 번 더 확인한 값만 용량으로 씁니다.
- **넘치면 늘리고, 한가하면 줄입니다.** 용량 사용률과 대기열을 보고 복제본을 조절하고, 결정과 이유를 모두 기록합니다.
- **어디서든 같은 화면.** 운영자 앱은 macOS·Windows·Linux에서 원격 콘솔에 붙습니다. 토큰은 OS 키체인에만 둡니다.

RTX 3060 Ti(8GB) 한 장에서 실제로 돌린 결과입니다. 자세한 근거는 [docs/results/project2](docs/results/project2/summary.md)에 있습니다.

| 확인 | 결과 |
|---|---|
| GPU 한 장에 올라가는 복제본 | bf16 1.5B는 1개, 4비트 AWQ는 2개 |
| 복제본 1개 vs 2개 | 총 처리량은 같음(267k vs 273k 토큰/분). 열린 부하에서 SLO를 지킨 동시 사용자는 16 → 36명 |
| 자동 증감 | 사용률 110 %가 2분 → 1→2, 새 복제본 준비 110초, 유휴 → 2→1, 오류율 0 |
| 복제본 강제 종료 | 재기동 144초 동안 오류율 0 |
| 고객별 한도 | 한도를 넘는 요청은 429, 콘솔 집계와 게이트웨이 로그가 일치 |

<p align="center"><img src="docs/images/app_loadtest.png" alt="부하 시험: 시험 기록별 동시 사용자 대 첫 글자 p99" width="880"></p>

## 왜 이 앱을 쓰나

서빙 클러스터를 운영할 때 보통은 터미널의 `kubectl`, Grafana 대시보드, 부하 시험 스크립트, 과금 스프레드시트를 오가며 판단합니다. ttalkkak 콘솔은 그 판단에 필요한 것을 한 화면에 모읍니다.

| 운영자가 묻는 것 | 보통의 방법 | ttalkkak 콘솔 |
|---|---|---|
| 지금 서버가 얼마나 찼나? | GPU·큐 지표를 보고 감으로 판단 | **잰 용량** 대비 사용률. 그 용량이 어떤 부하 시험(언제, 복제본 몇 개, 어떤 SLO)에서 나왔는지 함께 보여 줌 |
| 몇 명까지 받을 수 있나? | 스크립트로 부하를 걸고 그래프를 눈으로 비교 | 버튼 하나로 goodput 탐색 → 열린 부하 확인. 신뢰 구간까지 붙은 단계 표와 시험별 겹친 그래프 |
| 왜 복제본이 늘었나? | 컨트롤러 로그를 뒤짐 | 결정마다 시각·이유·전후 복제본 수·준비 시간이 남는 결정 기록, OS 알림 |
| 누가 얼마나 썼고 얼마를 받나? | 게이트웨이 로그를 따로 집계 | 고객별 한도 소진률, 사용량과 추정 청구, CSV |
| 집 밖에서도 볼 수 있나? | VPN과 kubeconfig를 노트북에 둠 | 노트북에는 앱과 운영자 토큰만. 클러스터 자격 증명은 서버 밖으로 나가지 않음 |

손이 닿는 조치(복제본 끄기, 키 폐기, 부하 시험)는 무엇을 얼마나 하는지 문장으로 보여 주는 확인 창을 거칩니다. 위험한 평문 연결에는 따로 동의를 받습니다.

## 누가 무엇을 하나

| 누구 | 하는 일 | 쓰는 것 |
|---|---|---|
| 칩 회사 | 자기 칩과 추론 엔진을 칩 설정 파일로 끼워 넣습니다 | `chips/*.yaml` |
| 클라우드 사업자 | 모델 검증과 배포, 고객·키·한도, 원가·청구, 용량과 증감 | 콘솔 앱, `ttalkkak ops` |
| 이용자 | 터미널에서 에이전트와 대화합니다. 턴마다 토큰과 예상 요금이 보입니다 | `ttalkkak` |

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
| 몰려도 버티기 (복제본, 라우팅, 실시간 화면, 부하 시험, 자동 증감, 데스크톱 앱) | 구현됨 | 됨 (2026-11-17~18, RTX 3060 Ti, [결과](docs/results/project2/summary.md)) — 앱 일부 항목 미확인 |
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

## 부하 시험

콘솔(앱의 실시간 탭)의 부하 시험은 guidellm 0.8.1의 goodput 탐색으로 "요청의 95 %가 첫 글자 1초 안에 오는" 가장 높은 동시 사용자 수를 찾고(단계마다 30초, 처음 5초는 측정에서 제외), 찾은 수준에서 잰 요청 속도의 75 %로 60초 동안 열린 부하(포아송) 확인을 합니다. 확인을 통과한 실행이 실제로 처리한 토큰/분이 용량이 되고, 못 지키면 한 단계 낮은 수준으로 내려가 다시 확인합니다(최대 3번). 단계별 표, 중단 사유, 확인 결과, 처리량이 꺾이는 무릎이 `runs/loadtests/`와 화면에 남습니다. 최대 약 8분 걸립니다. 확인에 실패한 기록은 용량으로 쓰지 않습니다.

## 칩 설정 파일

칩마다 다른 값(감지 명령, 엔진 이미지, 쿠버네티스 자원 이름, 전력·지표 이름)은 코드가 아니라 `chips/*.yaml` 형식의 설정 파일에 있습니다. 기본으로 [src/ttalkkak/data/chips/](src/ttalkkak/data/chips/)에 `nvidia-gpu.yaml`, `amd-gpu.yaml`이 들어 있고, 값이 `<...>`인 견본 `example-npu.yaml`이 형식을 보여 줍니다. 새 칩은 파일 하나를 `~/.ttalkkak/chips/`(또는 `TTALKKAK_CHIPS_DIR`가 가리키는 폴더)에 두면 되고, `--chip <이름 또는 yaml 경로>`로 고릅니다(`ttalkkak ops export`, `ttalkkak ops ui`). 형식이 틀리면 칸 이름이 든 한국어 오류가 납니다.

## 데스크톱 콘솔 앱: 무엇을 하나

운영자가 자기 컴퓨터(macOS·Windows·Linux)에 설치해서, 다른 곳(GPU 서버)에서 도는 ttalkkak 콘솔 서버에 운영자 토큰으로 붙는 앱입니다(Tauri v2). 화면 파일은 브라우저 콘솔과 같고, 앱은 요청을 Rust 쪽에서만 보내 토큰이 화면(JS)에 닿지 않습니다.

| 탭 | 하는 일 |
|---|---|
| 실시간 | 2초마다 갱신(창이 가려지면 멈춤). 지금 상태(동시 요청, 첫 글자·토큰 간격 p99, 출력 속도, 전력, 시간당 전기료), 용량 사용률과 근거가 된 부하 시험, 최근 1시간 그래프, 용량 부족 신호(429·대기·놀고 있는 복제본), 고객별 한도 소진률, 복제본별 상태(끄기), 권장 조치, 자동 증감 스위치와 결정 기록, 부하 시험(시작, 단계 표, 시험별 겹친 그래프) |
| 개요 | 클러스터 파드 상태 |
| 원가 | 전력 측정 기반 토큰당 원가와 권장 가격 |
| 테넌트 | 고객 추가, API 키 발급·폐기(한 번만 보여 줌), 분당 토큰 한도 |
| 사용량 | 기간·고객별 사용량과 추정 청구, CSV 저장(앱은 Downloads 폴더) |
| 서빙 실행 | 모델과 skill을 골라 배포 구성 찾기 → 띄우기 → 재기 |

그 밖에: 연결 화면(주소·토큰 확인, 이유별 실패 문구), 토큰은 OS 키체인에 저장, 평문 HTTP 위험 동의, 네이티브 메뉴(서버 바꾸기·연결 끊기·새로 고침), 자동 증감 결정과 429 급증 OS 알림(끄기 가능), 밝은·어두운 테마는 OS를 따름.

실기 확인(2026-11-18, 맥 앱 → SSH 터널 → 데스크탑 콘솔): 연결, 탭 6개, 실시간 화면, 겹친 그래프, Cmd+V 토큰 붙여넣기, 키체인 저장, CSV 저장. 근거와 스크린샷은 [docs/results/project2/app/](docs/results/project2/app/README.md).

## 데스크톱 앱 내려받기

GitHub Actions의 `desktop` 워크플로가 macOS(.dmg, Apple Silicon·Intel), Windows(.msi), Linux(.AppImage, .deb) 설치 파일을 만듭니다. 저장소의 Actions 탭에서 실행을 열고 맨 아래 Artifacts에서 내 OS용 파일을 내려받습니다. 서명하지 않은 빌드라서 처음 열 때 경고가 뜹니다.

- **macOS**: .dmg를 열어 앱을 Applications로 옮긴 뒤, 앱을 우클릭하고 `열기`를 누릅니다. 그래도 막히면 시스템 설정 → 개인정보 보호 및 보안에서 `그래도 열기`를 누릅니다.
- **Windows**: SmartScreen 창에서 `추가 정보` → `실행`을 누릅니다.
- **Linux**: AppImage는 `chmod +x ttalkkak*.AppImage` 후 실행합니다. .deb은 `sudo apt install ./파일명.deb`로 설치합니다.

**서버에 연결**: 콘솔 서버를 도는 컴퓨터에서 운영자 토큰을 만들고 원격 모드로 엽니다. 앱에는 서버 주소와 이 토큰을 넣습니다.

```bash
ttalkkak ops token new                     # 토큰을 한 번 출력 (다시 보기: token show, 폐기: token revoke)
kubectl port-forward -n ttalkkak svc/prometheus 9090:9090 &   # 실시간 화면이 읽을 Prometheus
ttalkkak ops ui --remote --prometheus-url http://127.0.0.1:9090 --gateway-url http://<게이트웨이 주소>/v1
```

`--remote`는 `tailscale ip -4`가 알려 주는 tailnet 주소에만 열고(tailscale이 없으면 `--host`를 직접 줘야 시작됩니다), tailnet 안에서도 평문 HTTP라는 경고를 냅니다. 앱은 `http://100.x.x.x:8787` 같은 Tailscale 주소 말고 다른 `http://` 주소에는 위험 동의 체크를 요구합니다. 자세한 확인 절차는 [프로젝트 2 데스크탑 지시문](docs/desktop/project2-desktop.md)에 있습니다.


## 개발

```bash
uv run pytest -q
uv run ruff check .
```

GPU가 필요한 부분은 모두 주입 가능한 함수 뒤에 있고, 테스트는 가짜 Docker와 mock OpenAI 서버로 돌아갑니다. mock 서버는 정상 응답뿐 아니라 `<think>` 누출, 스트림 불일치, call 2개, 여러 청크로 쪼개진 인자처럼 일부러 망가뜨린 응답도 재생해서, 검사기가 그것을 잡아내는지 확인합니다.

- 단계별 설계와 계획: [docs/specs/](docs/specs/), [docs/plans/](docs/plans/)

## 추후 개선점

실기 검증과 리뷰에서 나온 것 중 아직 고치지 않은 것입니다. 자세한 내용은 [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

- 앱: 부하 시험 그래프 세로축 숫자 잘림, 원격에서 갱신 주기가 2초보다 김(응답 뒤 2초 대기), 연결이 끊겼을 때 오류 문구가 비어 있음, 서빙 실행 결과 목록에 `loadtests`·`autoscale`이 섞임
- 앱 미확인: OS 알림 전달, 평문 HTTP 동의 초기화, API 키 창 Esc, 앱에서 서빙 실행
- 사용량이 게이트웨이 재설치 뒤 비어 있음(접근 로그 보존, 원인 미확인)
- 준비 안 된 파드가 부하 시험 복제본 수에 들어감, 공유 모델 캐시 정리 없음, 서명 없는 설치 파일
- 한 GPU(8GB)에 bf16 1.5B는 복제본 1개, 4비트는 2개까지 — 2개로 나눠도 총 처리량은 같고(267k vs 273k 토큰/분) 열린 부하에서 버티는 동시 사용자만 늘었다. 여러 GPU에서의 확장은 아직

## 정직하게 적는 한계

- **실제 GPU에서 처음부터 끝까지 돌린 적이 없습니다.** 엔진 로그 형식, 게이트웨이 CRD 필드, 지표 이름은 소스와 문서로 확인했고 실행으로는 확인하지 않았습니다. 확인 항목은 [docs/desktop/stage5-desktop.md](docs/desktop/stage5-desktop.md)에 있습니다.
- **분산은 GPU 한 장 위의 복제본 여러 개입니다.** 기계 여러 대가 아닙니다.
- **다른 칩은 실물이 없습니다.** 실제로 끼워 본 것은 NVIDIA GPU뿐입니다.
- 잘못된 HF 모델 ID나 `config.json`이 없는 저장소는 아직 traceback으로 끝납니다.
- 저장소 폴더에서 실행해야 기본 skill을 찾습니다.

## 라이선스

[Apache License 2.0](LICENSE)
