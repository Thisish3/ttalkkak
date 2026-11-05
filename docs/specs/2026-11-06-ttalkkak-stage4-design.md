# ttalkkak 4단계 설계: 로컬 웹 GUI

- 작성일: 2026-11-06
- 상태: 사용자가 "알아서 진행"을 지시해 설계 결정을 위임함. 결정은 2절에 근거와 함께 적는다.
- 선행: 1~3단계. 처음 합의(1단계 대화)대로 GUI는 단계별 JSON을 읽는 **얇은 층**이다.

## 1. 목표

브라우저에서 "HF에서 모델 고르고 딸깍"이 된다.

1. 모델 검색: HF Hub에서 이름으로 찾고, 각 모델의 가중치가 **이 GPU에 들어가는지** 표시.
2. skill 고르기: `skills/` 아래 skill을 체크박스로.
3. 실행: `ttalkkak up`과 같은 흐름을 백그라운드로 돌리고 단계 진행(probe → plan → launch → verify → pick)을 표시.
4. 결과: 리포트(`report.md`), 후보 표, 내보내기(3단계 export) 버튼.

## 2. 결정 사항 (위임받아 결정)

| 항목 | 결정 | 이유 |
|---|---|---|
| 형태 | `ttalkkak ui`가 띄우는 로컬 웹 서버(기본 `127.0.0.1:8787`) | 1단계에서 합의한 "로컬 웹 앱". 원격 GPU 장비에서도 SSH 포트 포워딩으로 쓴다 |
| 서버 | Starlette + uvicorn | 이미 의존성에 있다(fastmcp 경유). 새 프레임워크를 들이지 않는다 |
| 화면 | 정적 HTML 1장 + 순수 JS(빌드 도구 없음) | 포트폴리오 데모에 충분하고, Python만으로 실행된다 |
| 실행 방식 | 실행 요청마다 백그라운드 스레드에서 `run_up` 호출. 동시에 1개만(GPU 한 장) | 1~3단계 파이프라인을 그대로 재사용 |
| 진행 표시 | 화면이 `GET /api/runs/<id>`를 2초마다 조회(`state.json`의 `done` 기반) | 파이프라인을 고치지 않고 진행을 보여 줄 수 있다 |
| 바인딩 | 기본 `127.0.0.1`만. `--host 0.0.0.0`은 명시할 때만, 경고 출력 | 컨테이너를 띄우고 코드 실행 skill이 있는 서버를 외부에 열지 않는다 |
| 인증 | 없음(로컬 전용) | 위 바인딩 정책으로 대신한다 |

## 3. API

| 메서드·경로 | 동작 |
|---|---|
| `GET /` | 정적 화면 |
| `GET /api/gpu` | `detect_gpu()` 결과(없으면 `null`) |
| `GET /api/models?q=<text>&limit=20` | HF `list_models(search=q, pipeline_tag="text-generation", sort="downloads", limit, expand=["safetensors", "downloads"])`(2026-11-06 확인: `m.safetensors.parameters`가 dtype별 개수) → 각 항목 `{id, downloads, fits: true/false/null, weight_gib}`. `fits`는 **가중치만** 기준(가중치 < 1단계 `usable_bytes`)이고, 가중치를 추정할 수 없으면(GGUF 등) `null`. KV까지 포함한 판정은 목록 API에 config가 없어 실행 시 probe가 한다 |
| `GET /api/skills` | `skills/`의 skill 목록과 요구사항 |
| `POST /api/runs` `{model, skills}` | 실행 시작. 이미 실행 중이면 409. 응답 `{run_id}` |
| `GET /api/runs` | `runs/` 아래 run 목록(최신순, 선택 결과 요약) |
| `GET /api/runs/<id>` | `{done, running, error, pick, report_md}` |
| `POST /api/runs/<id>/export` `{target, image?}` | 3단계 `write_export`로 `runs/<id>/export-<target>/`에 쓰고 파일 목록 반환 |

모델 크기 추정: HF 모델 정보의 `safetensors.total`(파라미터 수)과 `safetensors.parameters`의 dtype별 개수로 바이트를 계산한다(BF16/F16 2바이트, I8/U8 1바이트, I32·F32 4바이트, AWQ/GPTQ 패킹은 I32로 기록되므로 그대로 계산). 정보가 없으면 `fits=null`(화면에는 "?"). 정확한 판정은 실행할 때 probe가 한다.

## 4. 오류 처리

- HF 검색 실패(네트워크, rate limit): `502`와 메시지. 화면은 오류 문구를 보여 준다.
- 실행 중 `TtalkkakError`: run 상태에 `error`(메시지, 종료 코드)로 남기고 화면에 표시. 그 밖의 예외도 메시지만 남기고 서버는 계속 동작.
- 잘못된 run id(경로 이탈 `..` 포함): `404`. run id는 `runs/` 바로 아래 디렉터리 이름만 허용.
- 실행 중 서버 종료: 1단계의 정리 로직(컨테이너 정지)이 그대로 동작하도록 실행 스레드가 예외를 다시 올린다.

## 5. 테스트

Starlette `TestClient`로 API만 테스트한다. HF 검색, GPU 감지, `run_up`은 주입 가능한 함수로 받아 가짜로 바꾼다.

| 대상 | 방법 |
|---|---|
| 모델 검색 | 가짜 HF 응답(safetensors 정보 있음/없음) → `fits` true/false/null |
| 크기 추정 | dtype별 파라미터 수 → 바이트 |
| 실행 | `POST /api/runs`가 가짜 `run_up`을 스레드로 호출, 두 번째 요청은 409, 완료 후 `GET`에 pick·report |
| 경로 이탈 | `/api/runs/..%2F..` → 404 |
| export | run fixture로 `POST .../export` → 파일 생성 |
| 화면 | `GET /`가 HTML을 돌려주고 API 경로 문자열을 포함 |

사람이 하는 마지막 작업: 데스크탑에서 `ttalkkak ui`로 모델 검색 → 실행 → 리포트 → export까지 브라우저로 한 번 돌리고 화면을 캡처한다.

## 6. 범위 밖

로그인·다중 사용자, 실행 취소 버튼(1단계에서 Ctrl-C만 정리 동작), 채팅 화면(`ttalkkak chat` CLI 사용), 실시간 로그 스트리밍.
