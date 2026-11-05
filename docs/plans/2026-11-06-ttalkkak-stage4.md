# ttalkkak 4단계 (로컬 웹 GUI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `ttalkkak ui`가 로컬 웹 화면을 띄워 HF 모델 검색 → skill 선택 → 실행 → 리포트 → export를 브라우저에서 한다.

**Architecture:** `ui/app.py`의 `create_app(root, deps)`가 Starlette 앱을 만든다. 외부 의존(HF 검색, GPU 감지, 파이프라인 실행)은 `UiDeps`로 주입한다. 실행은 `RunManager`가 스레드 1개로 관리한다. 화면은 `ui/static/index.html` 한 장.

**Tech Stack:** Starlette 1.10.0, uvicorn 0.54.0(이미 설치됨, pyproject에 명시 고정), huggingface-hub 2.4.0.

**Spec:** `docs/specs/2026-11-06-ttalkkak-stage4-design.md`

## Global Constraints

- 기본 바인딩 `127.0.0.1:8787`. `0.0.0.0`은 `--host`로만, 경고 출력.
- 동시 실행 1개. 두 번째 `POST /api/runs`는 409.
- run id는 `runs/` 바로 아래 디렉터리 이름(`[A-Za-z0-9._-]+`, `..` 불가)만 허용, 아니면 404.
- 모델 크기: BF16/F16/FP16 2바이트, I8/U8/F8_E4M3/F8_E5M2 1바이트, F32 4바이트, I32 0.5바이트(HF가 int4 패킹 가중치를 풀어 놓은 개수로 기록), 그 외 2바이트로 보수 추정.

## Review Focus

1. **HF 검색 실패** → 502 + 메시지, 서버 계속 동작.
2. **실행 중 예외**(TtalkkakError 또는 기타) → run 상태 `error`에 남고 RunManager가 다음 실행을 받는다.
3. **경로 이탈 run id** → 404.
4. **이미 실행 중** → 409, 기존 실행 영향 없음.
5. **외부 바인딩** → 명시할 때만, 경고.

## Tasks

### Task 1: 크기 추정과 모델 검색

**Files:** Create `src/ttalkkak/ui/__init__.py`, `src/ttalkkak/ui/models.py`, `tests/test_ui_models.py`

**Produces:** `weight_bytes(parameters: dict[str, int]) -> int`, `search_models(q, limit, gpu, list_models) -> list[dict]`(각 `{id, downloads, weight_gib, fits}`).

- [ ] 실패하는 테스트: dtype 혼합 바이트 계산; safetensors 없는 모델 → `fits None`; 8GB에 1.5B BF16 → True, 70B → False; gpu None → `fits None`.
- [ ] 구현 → 통과 → 커밋 "Estimate model size for the UI model search".

### Task 2: RunManager

**Files:** Create `src/ttalkkak/ui/runs.py`, `tests/test_ui_runs.py`

**Produces:** `RunManager(runs_root, run_fn)`: `start(model, skills) -> str`(실행 중이면 `Busy` 예외), `status(run_id) -> dict`(`done`, `running`, `error`, `pick`, `report_md`), `list() -> list[dict]`, `safe_dir(run_id) -> Path`(규칙 위반 시 `KeyError`).

- [ ] 실패하는 테스트: start가 스레드에서 run_fn 호출·완료 후 status에 pick; 실행 중 두 번째 start → Busy; run_fn이 TtalkkakError → status error에 메시지·코드, 이후 start 가능; `safe_dir("..")`·`"a/b"` → KeyError.
- [ ] 구현 → 통과 → 커밋 "Run pipelines in the background for the UI".

### Task 3: Starlette 앱과 화면

**Files:** Create `src/ttalkkak/ui/app.py`, `src/ttalkkak/ui/static/index.html`, `tests/test_ui_app.py`; Modify `src/ttalkkak/cli.py`(`ttalkkak ui`), `pyproject.toml`(`starlette==1.10.0`, `uvicorn==0.54.0`, package data에 static 포함)

**Produces:** `create_app(root: Path, deps: UiDeps) -> Starlette`, `UiDeps(list_models, detect_gpu, run_fn)`.

- [ ] 실패하는 테스트(TestClient): `/` HTML에 `/api/models` 문자열; `/api/gpu`; `/api/models?q=` 정상·HF 예외 → 502; `/api/skills`; `POST /api/runs` → run_id, 두 번째 409; `/api/runs/<id>`; 경로 이탈 404; `POST /api/runs/<id>/export` → 파일 목록.
- [ ] 구현(화면: 검색창·결과 표(fits 배지)·skill 체크박스·실행 버튼·진행 단계·리포트 `<pre>`·export 선택) → 통과 → 커밋 "Add the ttalkkak ui web app".

### Task 4: 브라우저 확인 (사람이 실행)

- [ ] 데스크탑에서 `uv run ttalkkak ui` → 검색·실행·리포트·export 한 번, 화면 캡처를 `docs/notes/`에.
