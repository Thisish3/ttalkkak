# ttalkkak 5단계 설계: 추론 서비스 운영 (게이트웨이·원가·모니터링·테넌트 관리)

- 작성일: 2026-11-11
- 상태: 사용자 리뷰 대기
- 선행: 1~4단계(main `02d4c6d`), UI 정리 브랜치 `ui-polish`(`fe9990c`).
- 사용자 합의(2026-11-11 대화):
  - 사용자는 **GPU를 사서 추론 API를 파는 운영자**다. 그 운영자의 고객(최종 사용자)은 6단계의 콘솔 에이전트로 서비스를 쓴다.
  - 게이트웨이는 새로 만들지 않고 **오픈소스를 엮는다**(접근 A: Envoy AI Gateway + llm-d + vLLM, Prometheus·Grafana·OpenTelemetry).
  - **요금은 합리적인 추정 수준**(원가 측정 → 권장 가격 → 청구서 CSV). 실제 결제·요금제·선불은 하지 않는다.
  - **기능은 제품 수준으로**: 설치부터 운영, 관리 화면, 오류 처리, 문서까지.

## 1. 목표

운영자가 ttalkkak 하나로 다음을 한다.

1. **원가와 용량**: 내 GPU에서 이 모델의 입력·출력 토큰 하나에 드는 전력과 원가, 응답 속도 목표(SLO)를 지키며 낼 수 있는 처리량, 목표 트래픽에 필요한 GPU 수를 잰다.
2. **서비스 구성**: 게이트웨이(키 인증·토큰 기록·한도) → llm-d 라우터(KV 캐시 인식) → vLLM 복제본을 쿠버네티스에 한 번에 올린다.
3. **테넌트 관리**: 고객과 API 키를 만들고, 교체·폐기하고, 고객별 토큰 한도와 캐시 격리 정책을 정한다.
4. **실시간 운영**: GPU·서비스 품질·사용량/비용 대시보드, 경보, 요청 단위 추적을 본다.
5. **청구 근거**: 기간별 고객 사용량을 집계해 청구서 CSV와 OpenMeter 형식(CloudEvents)으로 낸다.
6. **연결 지점**: 6단계 콘솔 에이전트가 쓸 가격표 조회(`/v1/pricing`)와 키별 사용량 조회(`/v1/usage`).

## 2. 구성

```
고객(API 키) ─▶ Envoy AI Gateway ─▶ llm-d 라우터(EPP) ─▶ vLLM 복제본
                 │ 키 인증·cache_salt 주입·토큰 기록·토큰 한도(Redis)
                 │ 접근 로그(본문 없음) ─▶ ttalkkak bill ─▶ 청구서 CSV / CloudEvents
                 ▼
     Prometheus ◀─ vLLM /metrics, 게이트웨이 지표, EPP 지표, GPU exporter
         │
         ▼
      Grafana(대시보드 3개 + 경보)      OpenTelemetry Collector ─▶ Jaeger(요청 추적)

ttalkkak cost ─▶ cost.json(토큰당 원가·권장 가격·필요 GPU 수) ─▶ export·bill·UI
ttalkkak tenants ─▶ 테넌트·키 저장소(SQLite) ─▶ 게이트웨이 설정(Secret)·bill
ttalkkak ops ui ─▶ 관리 화면(테넌트·키·원가·사용량·링크)
```

### 2.1 새 명령 (운영자용, 2.2절의 `ops` 아래)

| 명령 | 하는 일 |
|---|---|
| `ttalkkak ops cost --run <run> [--config cost.yaml]` | 3절. 이미 띄운 엔진(또는 `up`이 고른 후보)에 부하를 걸고 전력을 기록해 원가표를 만든다 |
| `ttalkkak ops tenants add/list/key-issue/key-rotate/key-revoke/set-limit/set-cache` | 5절. 테넌트와 키 관리 |
| `ttalkkak ops export gateway --run <run> --out <dir>` | 4절. 쿠버네티스 매니페스트·Grafana 대시보드·경보·OTel 설정 생성 |
| `ttalkkak ops cluster up/down/status --out <dir>` | 생성한 매니페스트를 순서대로 적용·삭제하고, 구성요소 상태를 표로 보여 준다(설치 경험을 제품처럼) |
| `ttalkkak ops bill --logs <path> --from <date> --to <date>` | 6절. 청구서와 CloudEvents |
| `ttalkkak ops ui` | 7절. 운영자 관리 화면(4단계 UI에 운영 탭 추가) |

## 2.2 진입점과 설치 (6단계와 공유하는 결정)

사용자 합의(2026-11-11): **최종 사용자는 콘솔에서 `ttalkkak`만 치면 바로 쓰고, 설치는 간단해야 한다.**

- 명령 구조를 둘로 나눈다(아직 배포 전이라 기존 명령 이름을 바꿔도 된다):
  - `ttalkkak` (인자 없음): **사용자용 콘솔 에이전트**(6단계). 처음 실행이면 접속 설정을 묻는다(게이트웨이 주소, API 키) → `~/.ttalkkak/config.toml`(권한 0600)에 저장 → 바로 대화 화면. `ttalkkak login`/`logout`으로 바꿀 수 있다.
  - `ttalkkak ops <명령>`: **운영자용** 명령. 기존 `up`, `chat`, `export`, `ui`와 이 문서의 `cost`, `tenants`, `cluster`, `bill`, `serve`가 여기로 들어간다. 기존 최상위 명령(`ttalkkak up` 등)은 한 버전 동안 `ops`로 넘기는 별칭과 안내 문구를 남긴다.
- 설치:
  - 사용자: `uv tool install ttalkkak` 또는 `pipx install ttalkkak` 한 줄. 기본 설치는 **가벼운 클라이언트**만(에이전트 런타임, OpenAI 호환 클라이언트, 도구) 들어간다.
  - 운영자: `uv tool install "ttalkkak[ops]"`. guidellm, lm-eval, Starlette UI, 쿠버네티스 생성 코드 등 무거운 의존성은 `ops` extra로 옮긴다.
  - 설치 스크립트(`curl ... | sh`)는 만들지 않는다(uv/pipx가 같은 일을 하고, 스크립트 배포는 검증 부담이 크다).
- PyPI 이름 `ttalkkak`은 비어 있음을 2026-11-05에 확인했다. 실제 업로드는 사용자 결정 전까지 하지 않는다(TestPyPI로 설치 경험만 검증).
- 이 절의 구조 변경(명령 재배치, extra 분리)은 5단계 구현 계획의 첫 작업으로 한다. 사용자 에이전트 자체는 6단계다.

## 3. 원가와 용량 (`ttalkkak cost`)

### 3.1 측정
- 부하 도구: guidellm(이미 의존성). 동시 요청 수 1, 2, 4, 8, 16(GPU 메모리가 허용하는 데까지) 각각 고정 시간 동안.
- 부하 두 종류:
  - A(생성 위주): 입력 256, 출력 128 토큰.
  - B(입력 처리 위주): 입력 2048, 출력 1 토큰.
  - C(캐시 적중): A와 같은 앞부분 1792 토큰을 공유하는 요청(같은 cache_salt). 캐시된 입력 토큰의 원가용.
- 전력: `nvidia-smi --query-gpu=timestamp,power.draw,clocks.sm,temperature.gpu --format=csv -lms 500`을 측정 동안 기록하고, 사다리꼴 적분으로 구간 에너지 E(J)를 구한다. 측정 시작·끝 시각은 guidellm 결과의 시각을 쓴다.
- 클럭은 고정하지 않는다(운영 환경의 실제 전력을 재는 것이 목적). 대신 측정 중 클럭·온도 로그를 함께 남긴다.

### 3.2 계산
- 토큰당 에너지: `E_A = e_in·N_in,A + e_out·N_out,A`, `E_B = e_in·N_in,B + e_out·N_out,B`를 풀어 `e_in`, `e_out`(J/토큰). 캐시 입력 `e_cached`는 C에서 `e_out`과 신규 입력 몫을 뺀 나머지를 캐시 토큰 수로 나눈다. 음수가 나오면 0으로 두고 리포트에 경고.
- 연립 해가 불안정할 때(행렬식이 0에 가깝거나 해가 음수): 경고하고 `e_in = E_B / N_in,B`, `e_out = (E_A − e_in·N_in,A) / N_out,A`로 대체.
- 전기료(원/토큰): `e / 3.6e6 × 전기료(원/kWh) × PUE`.
- 장비 상각(원/초): `GPU 가격 ÷ (상각 연수 × 365 × 86400 × 가동률)`. 토큰당 상각비는 SLO 운영점의 처리량으로 나누고, 입력·출력 분배는 에너지 비율을 따른다(근사, 리포트에 명시).
- 권장 가격(원/100만 토큰): `(전기료 + 상각비) × (1 + 마진)`, 입력·캐시 입력·출력 각각.
- 운영점: 응답 속도 목표(`ttft_p99_ms`, `itl_p99_ms`)를 지키는 가장 큰 동시 요청 수. 없으면 "이 GPU로는 SLO를 지킬 수 없음"으로 끝내고 가장 낮은 동시성 결과를 참고로 낸다.
- 필요 GPU 수: `ceil(목표 출력 토큰/초 ÷ 운영점 출력 토큰/초)`. 목표 트래픽이 없으면 생략.

### 3.3 입력과 출력
- `cost.yaml`(없으면 기본값을 만들어 주고 그 값을 리포트에 표시):
```yaml
gpu_price_krw: 600000
amortization_years: 3
utilization: 0.6
electricity_krw_per_kwh: 150
pue: 1.4
margin: 0.3
slo: {ttft_p99_ms: 1000, itl_p99_ms: 100}
target_output_tok_s: null
```
- 결과 `runs/<run>/cost.json`과 `cost.md`(표와 가정, 한계). 한계: GPU 보드 전력만 잼(나머지는 PUE로 대신), 작업량에 따라 원가가 달라짐, 상각 분배는 근사.

## 4. 서비스 구성 (`export gateway`, `cluster up`)

### 4.1 생성물 (`<out>/`)
- `00-namespace.yaml`, `10-redis.yaml`
- `20-vllm.yaml`: Deployment(복제본 수 = cost.json의 필요 GPU 수, 없으면 1), 운영점의 엔진 인자, `--enable-prompt-tokens-details`류 옵션(캐시 토큰을 usage에 넣는 옵션, 이름은 단계 0에서 확인), Prometheus 수집 주석.
- `30-llmd.yaml`: InferencePool, EPP(Endpoint Picker), 복제본 선택 정책(KV 캐시·대기열).
- `40-gateway.yaml`: Envoy Gateway의 Gateway, Envoy AI Gateway의 AIGatewayRoute(`llmRequestCosts`: 입력, 캐시 입력, 출력), API 키 인증(SecurityPolicy, 키 → 테넌트 헤더), cache_salt 주입(테넌트별 정책), 토큰 한도(BackendTrafficPolicy + 전역 rate limit), 접근 로그 형식(6.1).
- `50-observability.yaml`: Prometheus, Grafana(대시보드·경보 프로비저닝), OpenTelemetry Collector, Jaeger, GPU exporter.
- `grafana/*.json` 대시보드 3개, `alerts.yaml`.
- `README.md`: 이 묶음을 손으로 적용하는 방법과 각 파일의 역할.

### 4.2 `cluster up`
- 파일 번호 순서로 `kubectl apply`, 각 단계 뒤에 준비 상태를 기다린다(시간 제한, 실패하면 어느 구성요소가 왜 실패했는지 `kubectl describe` 요약과 함께 멈춤).
- `status`: 구성요소별 준비 상태, 게이트웨이 주소, Grafana·Jaeger 주소, 엔드포인트 시험 요청 결과를 표로.
- `down`: 네임스페이스 삭제(확인 프롬프트, `--yes`로 생략).

### 4.3 버전
- Envoy Gateway, Envoy AI Gateway, llm-d(Gateway API Inference Extension), Redis, Prometheus, Grafana, OTel Collector, Jaeger, GPU exporter 버전은 구현 계획 단계 0에서 데스크탑에서 동작을 확인한 뒤 고정하고 이 문서 끝 "환경 기록"에 적는다. 문서마다 필드 이름이 다르므로(Envoy AI Gateway 0.x ~ 1.0), 고정한 버전의 문서만 따른다.

## 5. 테넌트와 키 (`ttalkkak tenants`)

- 저장소: `~/.ttalkkak/tenants.db`(SQLite). 테이블 `tenants(id, name, created_at, token_limit_per_min, cache_mode, salt_version)`, `keys(id, tenant_id, prefix, hash, created_at, revoked_at)`.
- API 키는 SDK·자동화용이다(사람은 5.1의 로그인).
- 키: 발급할 때만 원문을 한 번 보여 주고, 저장은 해시(SHA-256)와 앞 8자만. 형식 `tk_<tenant>_<random32>`.
- `key-rotate`: 새 키 발급 + 이전 키에 만료 시각(기본 24시간 뒤)을 건다. `key-revoke`: 즉시 폐기.
- `set-cache isolated|shared`: 격리(기본)면 게이트웨이가 `cache_salt = hash(tenant_id, salt_version)`을 요청에 넣는다. `rotate-salt`는 `salt_version`을 올려 기존 캐시와 더는 맞지 않게 한다(데이터 삭제 요청 대응).
- 게이트웨이 반영: 변경 후 `ttalkkak tenants sync`(또는 `cluster up` 때 자동)가 키 해시 목록과 테넌트 정책을 쿠버네티스 Secret/설정으로 갱신.
- 6단계 연결: 게이트웨이가 `/v1/pricing`(cost.json의 권장 가격)과 `/v1/usage`(그 키의 오늘·이번 달 토큰과 예상 금액)를 제공한다. 구현은 게이트웨이 뒤의 작은 ttalkkak 서비스(`ttalkkak ops serve`)이며, 같은 Gateway로 라우팅한다.

## 5.1 로그인 (사람) + API 키 (프로그램)

사용자 합의(2026-11-11): **브라우저 로그인과 API 키를 둘 다** 지원한다.

- 인증 서버(OIDC 제공자): 오픈소스 **Dex**를 우선으로 한다(가볍고 쿠버네티스 친화적). 단계 0에서 Dex가 디바이스 코드 흐름(RFC 8628)을 지원하는지 확인하고, 안 되면 **Keycloak**으로 바꾼다(LEDGER에 기록). 사용자 계정은 인증 서버의 로컬 계정(정적 사용자)으로 시작한다.
- 계정 → 테넌트: 인증 서버의 그룹을 테넌트 ID로 쓴다(`groups` 클레임). 한 사용자는 테넌트 하나에만 속한다(여러 개면 거절).
- 콘솔 로그인: `ttalkkak login`(처음 `ttalkkak` 실행 시 자동) → 디바이스 코드와 주소를 보여 주고 브라우저를 연다 → 사용자가 인증 서버에서 로그인 → 클라이언트가 토큰을 받아 `~/.ttalkkak/credentials.json`(0600)에 저장. 접근 토큰 수명 15분, 갱신 토큰으로 자동 갱신. `ttalkkak logout`은 갱신 토큰을 폐기하고 파일을 지운다. `ttalkkak whoami`는 사용자·테넌트·남은 수명을 보여 준다.
- 게이트웨이: Envoy Gateway SecurityPolicy로 **JWT(인증 서버 JWKS)와 API 키 둘 다** 받는다. 어느 쪽이든 테넌트 헤더(`x-ttalkkak-tenant`)와 주체 헤더(`x-ttalkkak-subject`: 사용자 ID 또는 키 접두어)로 바꿔 뒤로 넘긴다. 두 정책을 한 라우트에 같이 거는 방법은 단계 0에서 고정한 버전의 문서로 확정한다(안 되면 같은 Gateway에 경로를 둘로 나눈다).
- 사용량·청구는 테넌트 단위로 합치고, 주체별 내역도 남긴다.
- 운영자: `ttalkkak ops tenants user-add/user-remove`가 인증 서버 사용자와 그룹을 관리한다(Dex는 설정 파일 갱신 후 재시작, Keycloak은 Admin API).

## 6. 청구 근거 (`ttalkkak bill`)

### 6.1 접근 로그
- 게이트웨이 접근 로그(JSON 줄) 필드: `ts, request_id, tenant, model, status, input_tokens, cached_input_tokens, output_tokens, duration_ms`. **프롬프트·응답 본문은 남기지 않는다.**
- 로그 수집: 게이트웨이 파드 로그를 `ttalkkak bill --from-cluster`가 직접 읽거나, 파일 경로를 받는다.

### 6.2 집계
- 기간 필터, `request_id` 중복 제거, 5xx 제외(429는 토큰 0), 테넌트·모델별 합계.
- 금액 = 토큰 × cost.json 권장 가격(테넌트별 할인율 덮어쓰기 가능, `bill.yaml`).
- 출력: `invoice-<from>_<to>.csv`(테넌트, 모델, 입력·캐시 입력·출력 토큰, 단가, 금액, 합계), `usage-<from>_<to>.cloudevents.jsonl`(OpenMeter 이벤트 형식).
- 보관: `--retention-days`(기본 120)보다 오래된 로그 파일을 지운다(명시적 플래그 `--prune` 필요).

## 7. 실시간 운영 화면

### 7.1 Grafana 대시보드
1. **GPU 현황**: 장비별 사용률, 전력, 메모리, 온도, 클럭.
2. **서비스 품질**: 첫 토큰까지 시간·토큰 간 간격 p50/p99(SLO 기준선), 실행·대기 중 요청, KV 캐시 사용률, 초당 토큰, 오류율, 복제본별 분포.
3. **사용량·비용**: 테넌트별 초당·오늘 누적 토큰, 캐시 적중률, 실시간 누적 예상 금액(토큰 × 가격), 실시간 전기료(전력 × 전기료 × PUE), 그 차이(예상 마진).
- 경보: SLO 위반(5분 지속), KV 캐시 90% 초과, 테넌트 한도 80% 도달, GPU 온도, 엔진 다운.
- 지표 이름은 단계 0에서 실제 `/metrics`를 확인해 맞춘다. 게이트웨이 지표에 테넌트 라벨이 없으면 접근 로그 기반 집계로 대체하고 그 사실을 기록한다.

### 7.2 요청 추적
- 게이트웨이 → EPP → vLLM 구간을 OpenTelemetry로 잇고 Jaeger에서 본다. vLLM의 OTel 옵션 이름과 llm-d EPP의 추적 지원 여부는 단계 0에서 확인한다(지원하지 않으면 게이트웨이·vLLM 두 구간만).

### 7.3 ttalkkak 운영 화면 (`ops ui`)
- 4단계 UI와 같은 스타일에 탭 추가: **개요**(구성요소 상태, Grafana·Jaeger 링크), **원가**(cost.json 카드와 표), **테넌트**(목록, 키 발급·교체·폐기, 한도·캐시 정책), **사용량**(기간별 집계, 청구서 내려받기).
- 키 원문은 발급 직후 한 번만 화면에 보이고 다시 볼 수 없다.

## 8. 데이터 원칙

- 게이트웨이·ttalkkak 어디에도 프롬프트·응답 본문을 저장하지 않는다(로그, 추적 속성 포함). 추적에는 토큰 수·모델·테넌트만.
- 키는 해시로만 저장.
- KV 캐시를 CPU·디스크로 내리는 기능(LMCache 등)은 끈다. 켜면 고객 데이터가 GPU 밖에 남는다는 점을 README에 적는다.
- 사용량 기록은 청구 근거라 보관 기간 동안 남는다. 테넌트 캐시는 salt 교체로 무효화한다.

## 9. 테스트

| 대상 | 방법 |
|---|---|
| 원가 계산 | 단위 테스트: 연립 해, 불안정할 때 대체, 음수 처리, 운영점 선택, 필요 GPU 수, 기본 설정 생성 |
| 전력 기록 | 가짜 `nvidia-smi` 출력으로 적분, 구간 앞뒤 자르기, 빈 기록 |
| 테넌트 | 키 해시 저장, 원문 미저장, 교체 만료, 폐기, salt 교체 |
| 로그인 | 가짜 OIDC 서버(테스트용 Starlette)로 디바이스 흐름, 대기(`authorization_pending`)·느려짐(`slow_down`)·만료·거절, 토큰 갱신, 로그아웃 폐기, 자격 파일 권한 0600 |
| 배포 생성 | 생성 YAML을 기대 파일과 비교, 데스크탑에서 `kubectl apply --dry-run=server` |
| 청구 | 고정 로그로 필터·중복·실패 제외·할인·CSV·CloudEvents |
| ops API | Starlette TestClient: `/v1/pricing`, `/v1/usage`, 관리 API(로컬 전용 바인딩과 기존 보안 미들웨어 재사용) |
| 대시보드 | JSON 구조와 쿼리의 지표 이름이 단계 0에서 기록한 이름과 같은지 |

## 10. 데스크탑 검증 (k3s, RTX 3060 Ti 1장)

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 0 | 구성요소 버전 고정과 동작 확인, 메모리 사용량 기록, DCGM exporter의 WSL 동작 여부, vLLM 캐시 토큰·OTel 옵션 이름 | 환경 기록 작성. 안 되는 것은 대체안과 함께 LEDGER에 |
| 1 | `cost`: Qwen2.5-0.5B 또는 1.5B | 원가표, 운영점, 전력·클럭 로그 |
| 2 | `cluster up` | `status`가 전부 준비, 테넌트 2명의 키로 요청 성공 |
| 2b | 로그인 | `ttalkkak login`으로 브라우저 로그인 후 같은 요청 성공, 로그아웃 후 401 |
| 3 | 토큰 한도 | 한도 초과 시 429 |
| 4 | 캐시 격리 | 같은 프롬프트: 다른 테넌트는 캐시 적중 0, 같은 테넌트는 적중(vLLM 지표·usage의 cached 토큰) |
| 5 | `bill` | 실제 로그로 청구서, 손으로 계산한 값과 일치 |
| 6 | 대시보드·경보·추적 | 세 대시보드에 실시간 값, 경보 로드, Jaeger에 요청 구간 |
| 7 | `ops ui` | 탭 4개 동작, 스크린샷 |

- 메모리: WSL에 보이는 메모리가 7 GB다. 단계 0에서 구성요소별 사용량을 재고, 모자라면 WSL 메모리 한도 조정(사용자 작업)이나 관측 구성요소 일부를 가볍게(예: Jaeger all-in-one, Prometheus 보관 기간 축소) 하는 결정을 LEDGER에 남긴다.
- 복제본 여러 개는 같은 GPU에 작은 모델 2개를 `gpu-memory-utilization`을 나눠 띄워 흉내 낸다. 안 되면 복제본 1개로 검증하고 그 사실을 적는다.

## 11. 범위 밖 (백로그)

- 6단계: 사용자 콘솔 에이전트(직접 만든 엔진 / LangChain 엔진, 같은 인터페이스).
- 7단계: 여러 모델·LoRA 어댑터, 카나리 배포와 되돌리기, 장애 시 다른 백엔드로 넘기기.
- 나중: 요금제·선불·결제 연동, OpenMeter 직접 연동, 콘텐츠 안전 필터, 배치 API, 테넌트별 KV 캐시 공간 할당, KV 캐시 오프로드, 여러 노드 실측, Dynamo 라우터 실측.

## 12. 작업 방식

- 브랜치 `stage5`(`ui-polish`에서 시작).
- 맥에서 GPU 없이 되는 부분(원가 계산, 테넌트, 청구, 배포·대시보드 생성, ops API·UI)을 먼저 TDD로 만들고, 데스크탑 검증(10절)은 hxccl B가 끝난 뒤 한다.

## 환경 기록

(10절 단계 0에서 채운다.)
