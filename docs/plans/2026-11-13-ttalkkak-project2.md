# ttalkkak 프로젝트 2 (몰려도 버티기) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 칩을 설정 파일 하나로 끼우고, 복제본 여러 개를 콘솔에서 실시간으로 보고(용량·여유 포함), 부하 시험을 돌리고, 용량 지표로 복제본 수를 자동으로 늘리고 줄이며, 콘솔 전체를 정식 제품 수준의 화면으로 다듬는다.

**Architecture:** 칩별 값은 `src/ttalkkak/data/chips/*.yaml`과 `ttalkkak.chips` 로더로 옮기고 기존 호출부는 그 로더만 본다. 실시간 지표는 콘솔 서버(`ops ui`)가 Prometheus와 게이트웨이 접근 로그를 읽어 한 번에 넘긴다. 자동 증감의 판단은 순수 함수(`autoscale.decide`)이고, 클러스터 안 `ops serve`가 그 함수를 주기적으로 돌려 `kubectl scale`과 같은 일을 쿠버네티스 API로 한다. 화면은 Tauri 데스크탑 앱(세 OS)이고, 앱은 운영자 토큰으로 콘솔 서버에 네트워크로 붙는다. 콘솔과 `ops serve`는 ConfigMap 두 개(용량 기록, 자동 증감 스위치)와 `ops serve` 로그(결정 기록)로만 주고받는다.

**Tech Stack:** Python 3.12, pydantic v2, Starlette, httpx, PyYAML, typer, guidellm(`measure` extra), kubectl / 쿠버네티스 API, Prometheus HTTP API, Tauri v2(Rust) 데스크탑 앱 + 정적 HTML/JS 화면(Node 빌드 없음), GitHub Actions(세 OS 설치 파일).

**Spec:** `docs/specs/2026-11-12-ttalkkak-project2-design.md` (4.2 "용량과 여유", 4.6 "자동 증감" 포함). 상위: `docs/specs/2026-11-12-ttalkkak-product-definition.md`.

**Branch:** `main`에서 `project2` 브랜치를 만든다. 데스크탑 문서는 `stage5` 브랜치를 쓰므로 `stage5`는 건드리지 않는다.

## Global Constraints

- 기존 테스트 295개가 모든 태스크 끝에서 그대로 통과한다 (예외: Task G가 R21에 따라 고정 단계 시험 test_stages_for*·test_runner_writes_record_and_capacity를 goodput 탐색 시험으로 바꿈). 칩 설정으로 옮긴 뒤 NVIDIA 동작은 바뀌지 않는다.
- 사용자에게 보이는 문구와 오류는 한국어. 설정 파일 오류에는 칸 이름을 넣는다.
- 칩 설정 파일 형식: 스펙 3.2절의 칸 이름(`name, vendor, detect, engines, docker, kubernetes.resource, telemetry.{power_watts, exporter_image, metrics.{utilization,power,memory_used,temperature}}, export.{llmd,kserve}`)을 그대로 쓴다.
- 부하 시험(R21·R22로 바뀜): guidellm goodput 탐색 — 동시 사용자 1명부터 두 배씩, 실패하면 반씩 좁힘(허용 폭 20 %), 단계마다 30초(처음 5초 워밍업 제외), 최대 10단계, 최대 사용자(기본 20, 상한 100)까지. 목표: 요청 95 %가 첫 글자 목표 안. 그다음 통과 수준에서 잰 요청 속도의 75 %로 60초 포아송 확인, 실패하면 낮은 통과 수준으로 내려가며 최대 3번. 한 번에 하나만. 기록은 runs/loadtests/<YYYYmmdd-HHMMSS>.json.
- 첫 글자 목표 기본 1000 ms. 용량 기간 1시간, 경계 90 %.
- 자동 증감 기본값: 늘리기 = 사용률 ≥ 80 % 2분 지속 또는 대기 > 0 2분 지속, 줄이기 = 사용률 ≤ 30 % 10분 지속 이고 대기 0, 유휴 복제본(5분 처리 0)이 있고 하나 줄였을 때 예상 사용률 ≤ 60 %이면 줄이기. 늘린 뒤 5분·줄인 뒤 10분 쿨다운. 한 번에 1개. 최소 1개, 최대 `max_replicas`. 새 복제본이 ready가 아니면 결정하지 않는다. 기본 켜짐.
- 메모리 나누기: 자동 증감을 켜면 `--gpu-memory-utilization = floor(0.85 / max_replicas, 2자리)`로 고정. 끄면 `floor(0.85 / replicas, 2자리)`.
- 결정 기록: `runs/autoscale/<YYYY-mm-dd>.jsonl` (콘솔 쪽), `ops serve` 표준 출력에 `AUTOSCALE <json>` 한 줄.
- 부하 시험 테넌트 id는 `ttalkkak-loadtest`이고 청구에서 빠진다.
- 위험한 조작(부하 시험, 복제본 수 변경, 복제본 끄기, 자동 증감 끄기)은 콘솔 확인 창을 거친다. 콘솔은 기본 `127.0.0.1`, `--allow-host`로 tailnet 이름만 더한다.
- 측정하지 않은 원인은 화면·문서 어디에도 추정으로 쓰지 않는다. 값이 없으면 "데이터 없음" 또는 스펙이 정한 문구를 띄운다.
- UI: 설치하는 데스크탑 앱(사용자 결정 2026-11-13: "브라우저는 많이 해봐서", 운영자 OS를 모르므로 macOS·Windows·Linux). 정식 앱 수준의 한 디자인 체계(색·간격·글꼴 토큰, 사이드바·카드·표·배지·모달·토스트·빈 상태·로딩·오류 상태)로 모든 화면을 통일. 외부 스크립트 없음, 차트는 인라인 SVG. 1024×700 최소 창에서 가로 스크롤 없음. 같은 화면 파일을 브라우저(127.0.0.1) 모드에서도 쓴다.
- 앱 ↔ 콘솔 서버: 네트워크 주소 + 운영자 토큰(`Authorization: Bearer`). 토큰 없이 원격 접속은 거절.

## Review Focus

1. Prometheus가 꺼져 있거나 질의가 빈 결과 → `/api/live`는 500이 아니라 섹션별 `error` 문자열과 나머지 값을 돌려주고, 화면은 그 카드만 "데이터 없음: <이유>"로 보인다. (Task 6)
2. 용량 기록이 지금 복제본 수·모델과 다름 → 사용률 숫자 대신 `needs_loadtest` 상태. 오래된 숫자를 보여 주지 않는다. (Task 5)
3. 부하 시험이 도는 중에 또 시작 → 409, 최대 사용자 0 또는 101 → 400, guidellm 실패 → 기록에 `error`가 남고 잠금이 풀린다. (Task 7)
4. 자동 증감 중 Prometheus 질의 실패 또는 용량 기록 없음 → 조치 없이 `hold`와 이유를 기록. (Task 8, 9)
5. 복제본 1개에서 줄이기 조건 / `max_replicas`에서 늘리기 조건 → 조치 없음, 이유 기록. (Task 8)

---

### Task 1: 칩 설정 파일 형식과 로더

**Files:**
- Create: `src/ttalkkak/chips.py`, `src/ttalkkak/data/chips/nvidia-gpu.yaml`, `src/ttalkkak/data/chips/amd-gpu.yaml`, `src/ttalkkak/data/chips/example-npu.yaml`
- Modify: `pyproject.toml` (package data에 `data/chips/*.yaml`)
- Test: `tests/test_chips.py`

**Interfaces:**
- Produces:
  - `class ChipConfig(BaseModel)`: `name: str`, `vendor: str`, `detect: Detect`, `engines: dict[str, EngineSpec]`, `docker: DockerSpec`, `kubernetes: K8sSpec`, `telemetry: Telemetry`, `export: dict[str, str] = {}`
  - `Detect`: `command: list[str]`, `parser: str | None = None` (`"nvidia-smi-csv"`, `"amd-smi-json"` 내장), `name: str | None`, `memory_mib: str | None` (JSON 경로)
  - `EngineSpec`: `image: str`, `api: Literal["openai"]`, `port: int = 8000`, `args: list[str] = []`, `health: str = "/health"`
  - `DockerSpec`: `flags: list[str] = []`, `devices: list[str] = []`; `K8sSpec`: `resource: str`
  - `Telemetry`: `power_watts: list[str] | None`, `exporter_image: str | None`, `metrics: dict[Literal["utilization","power","memory_used","temperature"], str] = {}`
  - `load_chip(path: Path) -> ChipConfig` (오류 시 `TtalkkakError`, 메시지에 파일명과 칸 경로, 예: `"chips/x.yaml: telemetry.metrics.power: ..."`)
  - `builtin_chips() -> list[ChipConfig]` (nvidia-gpu, amd-gpu 순서. example-npu는 포함하지 않음)
  - `chip_by_name(name: str) -> ChipConfig` (없으면 `TtalkkakError` "칩 설정을 찾을 수 없습니다: <name>")
  - `json_path(doc, path: str)` — `$.a.b[0].c` 형태만 지원, 없으면 `None`

- [ ] **Step 1: `git switch -c project2 main`**
- [ ] **Step 2: 실패하는 테스트 작성** — `test_builtin_values_match_old_constants`(nvidia: `engines["vllm"].image == "vllm/vllm-openai:v0.33.0"`, `engines["sglang"].image == "lmsysorg/sglang:v0.5.23"`, `docker.flags == ["--gpus","all"]`, `kubernetes.resource == "nvidia.com/gpu"`, `export == {"kserve": "ghcr.io/llm-d/llm-d-cuda:v0.9.0", "llmd.vllm": "gpu-vllm/release", "llmd.sglang": "gpu-sglang/release"}`, `telemetry.exporter_image == "nvcr.io/nvidia/k8s/dcgm-exporter:4.9.0"`, `telemetry.metrics["power"] == "DCGM_FI_DEV_POWER_USAGE"`; amd: 현재 `platform._IMAGES/_FLAGS`, `kserve._IMAGE["amd"]`, `llmd._COMPONENT` 값과 같고 `exporter_image is None`, `metrics == {}`), `test_example_npu_validates`, `test_missing_field_error_names_path`(engines 비운 파일 → 메시지에 `engines` 포함), `test_bad_metric_key_rejected`, `test_json_path`(`{"devices":[{"name":"X"}]}`와 `$.devices[0].name` → `"X"`, 없는 경로 → `None`), `test_unknown_chip_name`.
- [ ] **Step 3: 실행해서 실패 확인** — `.venv/bin/python -m pytest tests/test_chips.py -v` → ImportError
- [ ] **Step 4: 구현** — 값은 지금 `platform.py`, `export/kserve.py`, `export/llmd.py`, `gateway/versions.py`, `gateway/observability.py`, `cost/power.py`의 상수를 그대로 옮긴다. llm-d 컴포넌트 경로는 `export` 키 `llmd.<engine>`로 둔다. 오류 경로는 pydantic `ValidationError.errors()[i]["loc"]`를 점으로 이어 만든다.
- [ ] **Step 5: 실행해서 통과 + 전체** — `.venv/bin/python -m pytest -q` → 295 + 새 테스트 통과
- [ ] **Step 6: 커밋** — `git commit -m "Add chip config files and loader"`

### Task 2: 칩 설정으로 플랫폼·탐지·내보내기 바꾸기

**Files:**
- Modify: `src/ttalkkak/platform.py`, `src/ttalkkak/schemas.py` (`GpuInfo.vendor: str`, 새 칸 `chip: str = "nvidia-gpu"`), `src/ttalkkak/probe/gpu.py`, `src/ttalkkak/export/common.py`, `src/ttalkkak/export/kserve.py`, `src/ttalkkak/export/llmd.py`, `src/ttalkkak/export/dynamo.py`, `src/ttalkkak/ops_cli.py` / `src/ttalkkak/cli.py` (`--chip` 옵션), `src/ttalkkak/verify/applied.py`
- Test: `tests/test_platform.py`, `tests/test_probe.py`, `tests/test_export_targets.py` (기존 테스트는 그대로 두고 추가만)

**Interfaces:**
- Consumes: Task 1 `ChipConfig`, `builtin_chips`, `chip_by_name`, `load_chip`, `json_path`
- Produces:
  - `Platform(chip: ChipConfig)`; `Platform.image(engine) -> str | None`, `Platform.gpu_flags() -> list[str]`(`docker.flags` + `--device=<d>`), `Platform.from_gpu(g: GpuInfo | None) -> Platform`(g.chip으로 찾음, 없으면 nvidia-gpu)
  - `detect_gpu(run=subprocess.run, chips: list[ChipConfig] | None = None, prefer: str | None = None) -> GpuInfo | None` — 칩 순서대로 `detect.command` 실행, 내장 파서 또는 JSON 경로로 `GpuInfo(chip=<name>, vendor=<chip.vendor 소문자>)`. `prefer`가 있으면 그 칩만.
  - `gpu_key(vendor_or_chip: str) -> str` — 칩 이름 또는 기존 `"nvidia"/"amd"`를 받아 `kubernetes.resource`
  - `RunBundle.vendor`는 칩 이름을 담는다(기존 probe.json의 `"nvidia"/"amd"`는 각각 `nvidia-gpu`/`amd-gpu`로 해석)
  - `verify/applied.py`: 엔진이 vllm·sglang이 아니면 결과 `{"status": "unsupported", "reason": "이 엔진은 설정 적용 확인을 지원하지 않습니다"}`

- [ ] **Step 1: 실패하는 테스트** — `test_platform_from_chip_flags`(amd-gpu → 지금 `_FLAGS["amd"]`와 같음), `test_detect_json_chip`(example 형식의 가짜 칩 + `run`이 `{"devices":[{"name":"NPU-1","memory_mib":32768}]}` 반환 → `GpuInfo(name="NPU-1", memory_mib=32768, chip="example")`), `test_detect_prefer_chip`, `test_old_probe_vendor_maps_to_chip`(probe.json `vendor: "amd"` → `RunBundle.vendor == "amd-gpu"`), `test_export_llmd_component_from_chip`, `test_applied_check_unsupported_engine`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_platform.py tests/test_probe.py tests/test_export_targets.py -v`
- [ ] **Step 3: 구현** — 상수 dict를 지우고 칩 설정을 읽는다. `--chip`은 `ttalkkak ops run|ui|export`에 붙이고 `detect_gpu(prefer=...)`로 넘긴다.
- [ ] **Step 4: 전체 테스트** — `.venv/bin/python -m pytest -q` → 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "Read platform, probe and export values from chip configs"`

### Task 3: 전력·대시보드·경보를 칩 설정의 공통 지표 이름으로

**Files:**
- Modify: `src/ttalkkak/cost/power.py`, `src/ttalkkak/cost/runner.py`, `src/ttalkkak/gateway/observability.py`, `src/ttalkkak/gateway/versions.py`(DCGM 상수 제거)
- Test: `tests/test_observability.py`, `tests/test_cost_power.py`

**Interfaces:**
- Consumes: Task 1 `Telemetry`, Task 2 `Platform`/칩 이름
- Produces: `render_observability(..., chip: ChipConfig)`(기존 인자 유지 + 칩), `power_cmd(chip: ChipConfig) -> list[str] | None`

- [ ] **Step 1: 실패하는 테스트** — `test_nvidia_dashboards_unchanged`(nvidia-gpu로 만든 대시보드 JSON이 지금 출력과 같음: 기존 출력을 픽스처로 저장해 비교), `test_amd_no_exporter_and_panels_say_not_provided`(amd-gpu → DaemonSet `dcgm-exporter` 없음, 전력 패널 제목에 "이 칩은 이 지표를 내지 않음"), `test_alert_skipped_without_temperature_metric`, `test_power_cmd_none_for_chip_without_power`(cost 실행 시 `TtalkkakError` "이 칩은 전력 측정 명령이 없습니다").
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_observability.py tests/test_cost_power.py -v`
- [ ] **Step 3: 구현** — 패널 정의를 공통 이름으로 바꾸고 `metrics[key]`로 치환. 없는 칸은 Grafana text 패널. exporter는 `exporter_image`가 있을 때만.
- [ ] **Step 4: 전체 테스트** — 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "Map telemetry, dashboards and alerts through chip metric names"`

### Task 4: 복제본 메모리 나누기와 GPU 공유 방식

**Files:**
- Modify: `src/ttalkkak/gateway/manifests.py` (`GatewayOptions`, `_vllm`), `src/ttalkkak/ops_cli.py` (`ops export --target gateway`에 `--max-replicas`, `--autoscale/--no-autoscale`, `--gpu-share resource|runtime`)
- Test: `tests/test_gateway_manifests.py`

**Interfaces:**
- Produces: `GatewayOptions` 새 칸 `max_replicas: int = 1`, `autoscale: bool = True`, `gpu_share: Literal["resource","runtime"] = "resource"`; `memory_fraction(opt: GatewayOptions) -> float`; 렌더 결과에 ConfigMap `ttalkkak-autoscale`(`data: {enabled: "true"|"false", max_replicas: "<n>", deployment: "<vllm 배포 이름>"}`)와 빈 ConfigMap `ttalkkak-capacity`(`data: {"capacity.json": "{}"}`)

- [ ] **Step 1: 실패하는 테스트** — `test_memory_fraction_fixed_by_max_when_autoscale`(max 3 → vLLM args에 `--gpu-memory-utilization 0.28`), `test_memory_fraction_by_replicas_without_autoscale`(replicas 2, autoscale False → `0.42`), `test_replicas_cannot_exceed_max`(→ `TtalkkakError`), `test_runtime_share_drops_gpu_request`(`gpu_share="runtime"` → resources에 `nvidia.com/gpu` 없음, `runtimeClassName: nvidia`, env `NVIDIA_VISIBLE_DEVICES=all`), `test_autoscale_configmaps_rendered`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_gateway_manifests.py -v`
- [ ] **Step 3: 구현** — 기존 args에 `--gpu-memory-utilization`이 있으면 값을 바꾼다. `math.floor(x*100)/100`.
- [ ] **Step 4: 전체 테스트** — 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "Split GPU memory per replica and render autoscale config"`

### Task 5: 용량 계산 (순수 함수)

**Files:**
- Create: `src/ttalkkak/capacity.py`
- Test: `tests/test_capacity.py`

**Interfaces:**
- Produces:
  - `class Stage(BaseModel)`: `users: int`, `ttft_p99_ms: float`, `itl_p99_ms: float`, `output_tok_s: float`, `tokens_per_min: float`, `error_rate: float`
  - `class CapacityRecord(BaseModel)`: `loadtest_id: str`, `created_at: datetime`, `replicas: int`, `model: str`, `ttft_target_ms: float`, `max_tokens_per_min: float | None`
  - `capacity_from_stages(stages: list[Stage], target_ms: float) -> float | None` — ttft_p99 ≤ target이고 error_rate == 0인 단계 중 users가 가장 큰 단계의 `tokens_per_min`, 없으면 `None`
  - `utilization(now_tpm: float, rec: CapacityRecord | None, replicas: int, model: str) -> dict` — `{"state": "ok"|"needs_loadtest", "pct": float | None, "now_tpm", "max_tpm", "source": {...}}`. 기록 없음·`max_tokens_per_min is None`·replicas·model 불일치면 `needs_loadtest`
  - `period_summary(series: list[tuple[datetime, float]], edge: float = 90.0) -> dict` — `{"max", "avg", "over_edge": <90 이상으로 올라간 횟수(연속 구간은 1회)>}`, 빈 입력이면 값 `None`
  - `tenant_usage(records: Iterable[dict], limits: dict[str, int | None], now: datetime) -> list[dict]` — 최근 60초 토큰(input+output)과 한도 대비 %, 최근 1시간 429 수. tenant가 비었거나 `ttalkkak-loadtest`면 제외
  - `idle_replicas(running_by_pod: dict[str, list[tuple[datetime, float]]], now: datetime, minutes: int = 5) -> list[str]`
  - `cache_hit_rate(prompt_tokens: float, cached_tokens: float) -> float | None` (prompt 0이면 None)

- [ ] **Step 1: 실패하는 테스트** — 위 함수마다 하나 이상: `test_capacity_picks_highest_stage_within_target`(stages users 1/5/10/20, ttft 200/400/900/1500, target 1000 → users 10의 tpm), `test_capacity_none_when_all_over`, `test_capacity_ignores_stage_with_errors`, `test_utilization_needs_loadtest_on_replica_mismatch`, `test_utilization_pct`(9200/21500 → 42.79…), `test_period_summary_counts_crossings`([50,95,96,40,91] → over_edge 2), `test_tenant_usage_excludes_loadtest_and_empty`, `test_tenant_usage_counts_429_last_hour`, `test_idle_replicas_five_minutes`, `test_cache_hit_rate_zero_prompt`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_capacity.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인 + 전체**
- [ ] **Step 5: 커밋** — `git commit -m "Add capacity and headroom calculations"`

### Task 6: Prometheus 읽기와 `/api/live`

**Files:**
- Create: `src/ttalkkak/live/prom.py`, `src/ttalkkak/live/snapshot.py`
- Modify: `src/ttalkkak/ui/app.py` (`UiDeps.prometheus`, `UiDeps.kubectl`, `UiDeps.chip`), `src/ttalkkak/ui/ops_routes.py`, `src/ttalkkak/ops_cli.py` (`ops ui --prometheus-url`(기본 `http://127.0.0.1:9090`), `--allow-host`(여러 번), `--namespace`(기본 `ttalkkak`))
- Test: `tests/test_live.py`, `tests/test_ui_ops.py`

**Interfaces:**
- Consumes: Task 5 전부, Task 1 `ChipConfig.telemetry.metrics`, 기존 `UiDeps.gateway_logs`, `cluster.apply.status`
- Produces:
  - `class Prom`: `__init__(base_url: str, http: httpx.Client)`, `instant(q: str) -> list[tuple[dict, float]]`, `range(q: str, minutes: int, step_s: int) -> list[tuple[dict, list[tuple[datetime, float]]]]` — 연결 실패·HTTP 오류는 `PromError(str)`
  - 질의 상수(엔진 지표): `TPM = "sum(rate(vllm:prompt_tokens_total[1m]) + rate(vllm:generation_tokens_total[1m])) * 60"`, `RUNNING = "vllm:num_requests_running"`, `WAITING = "vllm:num_requests_waiting"`, `KV = "vllm:kv_cache_usage_perc"`, `PROMPT = "rate(vllm:prompt_tokens_total[5m])"`, `CACHED = "rate(vllm:prompt_tokens_cached_total[5m])"`, `TTFT_P99 = "histogram_quantile(0.99, sum(rate(vllm:time_to_first_token_seconds_bucket[1m])) by (le))"`, `ITL_P99`(같은 형태, `vllm:inter_token_latency_seconds_bucket`). 지표 이름은 데스크탑 Task 12에서 실제와 대조하고 다르면 이 상수만 고친다.
  - `build_snapshot(prom: Prom, pods: list[dict], logs: list[str], limits: dict, capacity: CapacityRecord | None, chip: ChipConfig, model: str, now: datetime, autoscale: dict) -> dict` — 키: `headline`(동시 요청, ttft_p99_ms, itl_p99_ms, output_tok_s, power_w, krw_per_hour), `capacity`(Task 5 `utilization` 결과 + `period` + `signals{rejected_429_1h, waiting, idle_replicas, ready_replicas, max_replicas}`), `tenants`, `replicas`(pod별 status/running/waiting/kv/hit_rate/gpu_util), `recommendations`(아래), `autoscale`(enabled, 최근 결정 10개), `errors{섹션: 메시지}`
  - 권장 조치(자동 아님): 복제본 KV 최대−최소 ≥ 30 %p → `{"kind":"router_weights","text":"한 복제본에 몰립니다. 라우터의 대기열·KV 가중치를 올리세요"}`, 고객 한도 소진 ≥ 95 % → `{"kind":"tenant_limit","tenant":id,"text":"<id> 고객이 한도를 다 쓰고 있습니다"}`
  - 라우트: `GET /api/live` → `build_snapshot`; 섹션 하나가 실패하면 `errors`에 넣고 나머지는 채운다
  - 용량 기록 읽기: `kubectl get configmap ttalkkak-capacity -n <ns> -o json`의 `capacity.json`
- [ ] **Step 1: 실패하는 테스트** — 가짜 `httpx.MockTransport`로: `test_prom_instant_parses_vector`, `test_prom_error_on_connection`, `test_snapshot_partial_when_prom_down`(errors에 `headline`, `capacity` 키, tenants는 채워짐), `test_snapshot_needs_loadtest_without_record`, `test_recommend_router_weights`, `test_recommend_tenant_limit`, `test_live_route_json`(UiDeps 가짜 주입), `test_allow_host_adds_name`(`Host: desk.tail1234.ts.net` 허용, 다른 이름 403).
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_live.py tests/test_ui_ops.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 전체 테스트**
- [ ] **Step 5: 커밋** — `git commit -m "Add live snapshot from Prometheus and gateway logs"`

### Task 7: 부하 시험과 복제본 조작

**Files:**
- Create: `src/ttalkkak/live/loadtest.py`
- Modify: `src/ttalkkak/ui/ops_routes.py`, `src/ttalkkak/billing/bill.py` (`ttalkkak-loadtest` 제외), `src/ttalkkak/cost/runner.py` (`guidellm_cmd`, `parse_points` 재사용, 필요하면 `error_rate`·`tokens_per_min` 계산 함수 추가)
- Test: `tests/test_loadtest.py`, `tests/test_bill.py`, `tests/test_ui_ops.py`

**Interfaces:**
- Consumes: Task 5 `Stage`, `CapacityRecord`, `capacity_from_stages`; 기존 `guidellm_cmd(base_url, model, data, out, streams, max_seconds)`, `TenantStore.add_tenant/issue_key`
- Produces:
  - `STAGES = [1, 5, 10, 20]`, `STAGE_SECONDS = 15`, `MAX_USERS = 100`
  - `stages_for(max_users: int) -> list[int]` — `STAGES` 중 ≤ max_users, 그리고 max_users가 목록에 없으면 끝에 추가 (예: 50 → [1,5,10,20,50]); 1~100 밖이면 `ValueError`
  - `class LoadTestRunner`: `__init__(root: Path, store, gateway_url: str, model: str, run=subprocess.run, now=datetime.now)`, `start(max_users: int, replicas: int, autoscale_on: bool) -> str`(이미 도는 중이면 `TtalkkakError(...,409)`), `status(id) -> dict`, `list() -> list[dict]`. 별도 스레드에서 단계마다 guidellm 한 번(`streams=[u]`, `max_seconds=15`, data `kind=synthetic_text,prompt_tokens=256,output_tokens=128`), 결과를 `runs/loadtests/<id>.json`(`{"id","created_at","replicas","model","autoscale_on","stages":[Stage...],"capacity_tpm","error"}`)에 저장. 끝나면 `CapacityRecord`를 ConfigMap `ttalkkak-capacity`에 `kubectl apply`(autoscale_on이 False인 시험만 용량 기록으로 쓴다).
  - 부하 시험 키: 테넌트 `ttalkkak-loadtest`가 없으면 만들고 키를 새로 발급, 시험 끝에 그 키를 폐기.
  - 라우트: `POST /api/loadtest {max_users, autoscale_on}` → `{id}`; `GET /api/loadtest/<id>`; `GET /api/loadtests`; `POST /api/replicas {count}` → `kubectl scale deployment/<vllm> --replicas=<count> -n <ns>`(1 ≤ count ≤ max_replicas, 아니면 400); `POST /api/replicas/<pod>/delete` → `kubectl delete pod <pod> -n <ns>`(pod 이름은 `^[a-z0-9-]+$` 그리고 vLLM 배포의 파드만)
- [ ] **Step 1: 실패하는 테스트** — `test_stages_for`(20 → [1,5,10,20], 7 → [1,5,7], 50 → [1,5,10,20,50]), `test_stages_for_out_of_range`, `test_runner_writes_record_and_capacity`(가짜 run이 guidellm 보고서 JSON을 써 줌 → 기록 파일, ConfigMap apply 호출 인자 확인), `test_runner_conflict_409`, `test_runner_records_error_and_unlocks`, `test_loadtest_key_revoked_after_run`, `test_bill_excludes_loadtest_tenant`, `test_scale_route_bounds`, `test_delete_pod_rejects_foreign_name`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_loadtest.py tests/test_bill.py tests/test_ui_ops.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 전체 테스트**
- [ ] **Step 5: 커밋** — `git commit -m "Add console load test and replica controls"`

### Task 8: 자동 증감 판단 (순수 함수)

**Files:**
- Create: `src/ttalkkak/autoscale.py`
- Test: `tests/test_autoscale.py`

**Interfaces:**
- Produces:
  - `class Rules(BaseModel)`: `up_pct=80.0`, `up_for_s=120`, `down_pct=30.0`, `down_for_s=600`, `idle_s=300`, `idle_after_pct=60.0`, `cool_up_s=300`, `cool_down_s=600`
  - `class Sample(BaseModel)`: `t: datetime`, `util_pct: float | None`, `waiting: int`, `ready: int`, `desired: int`, `idle_replicas: int`
  - `class State(BaseModel)`: `last_change: datetime | None = None`, `last_dir: Literal["up","down"] | None = None`, `history: list[Sample] = []` (최근 `max(up_for_s, down_for_s)`만 유지)
  - `class Decision(BaseModel)`: `t`, `action: Literal["up","down","hold"]`, `from_replicas: int`, `to_replicas: int`, `reason: str`(한국어, 숫자 포함, 예: "용량 사용률 84 %가 2분 넘게 계속")
  - `decide(state: State, s: Sample, rules: Rules, min_r: int = 1, max_r: int) -> tuple[Decision, State]`
  - 순서: `util_pct is None` → hold("용량 기록 없음 또는 지표 없음") → `ready < desired` → hold("새 복제본 준비 중") → 쿨다운 안 → hold → 늘리기 조건(창 안 모든 샘플이 조건 충족이고 창 길이 ≥ up_for_s) → `desired < max_r`이면 up 아니면 hold("최대 복제본") → 줄이기 조건 → `desired > min_r`면 down 아니면 hold → 유휴 조건(예상 사용률 = util × desired/(desired−1) ≤ idle_after_pct) → down → hold("조건 없음")
- [ ] **Step 1: 실패하는 테스트** — `test_hold_without_utilization`, `test_up_after_two_minutes_over_80`(119초 → hold, 121초 → up 1→2), `test_up_on_waiting_two_minutes`, `test_no_up_at_max`, `test_down_after_ten_minutes_under_30`, `test_no_down_below_one`, `test_idle_down_when_projected_ok`(util 25, desired 3, idle 1 → 예상 37.5 ≤ 60 → down), `test_idle_hold_when_projected_too_high`, `test_cooldown_after_up_blocks_down`, `test_hold_while_not_ready`, `test_one_step_at_a_time`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_autoscale.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 + 전체**
- [ ] **Step 5: 커밋** — `git commit -m "Add autoscale decision rules"`

### Task 9: `ops serve` 안의 자동 증감 루프와 결정 기록

**Files:**
- Create: `src/ttalkkak/ops_api/autoscaler.py`
- Modify: `src/ttalkkak/ops_api/app.py` (`build_serve_app`에 루프 시작), `src/ttalkkak/ops_cli.py` (`ops serve --prometheus-url`, `--autoscale-namespace`), `src/ttalkkak/gateway/manifests.py` (`_ops_serve`: 인자, Role `deployments/scale` get·patch와 `configmaps` get, `deployments` get·list, `pods` list), `src/ttalkkak/ui/ops_routes.py` (`POST /api/autoscale {enabled}`, `GET /api/autoscale/decisions`)
- Test: `tests/test_autoscaler_loop.py`, `tests/test_gateway_manifests.py`, `tests/test_ui_ops.py`

**Interfaces:**
- Consumes: Task 8 `decide`, `Rules`, `State`, `Sample`; Task 6 `Prom`, 질의 상수; Task 5 `utilization`, `idle_replicas`; Task 4 ConfigMap 이름
- Produces:
  - `class K8s`(얇은 클라이언트, 파드 안에서는 서비스 계정 토큰과 `https://kubernetes.default.svc`, 테스트에서는 가짜): `get_configmap(ns, name) -> dict`, `get_scale(ns, deploy) -> tuple[int, int]`(spec.replicas, status.readyReplicas), `set_scale(ns, deploy, n) -> None`
  - `tick(k8s, prom, state, rules, now, ns) -> tuple[Decision | None, State]` — `ttalkkak-autoscale.enabled != "true"`면 `None`(결정 없음), 아니면 샘플을 모아 `decide` 후 up/down이면 `set_scale`. 모든 결정을 `print("AUTOSCALE " + decision.model_dump_json(), flush=True)`. hold는 이유가 바뀔 때만 출력.
  - 루프 간격 10초 (판단: Prometheus 1분 rate 기반이라 2초는 의미 없음). 예외는 hold("오류: <메시지>")로 기록하고 계속.
  - 콘솔: `POST /api/autoscale {enabled: bool}` → ConfigMap `ttalkkak-autoscale`의 `enabled` patch. `GET /api/autoscale/decisions` → `kubectl logs deploy/ttalkkak-ops -n <ns> --since=24h`에서 `AUTOSCALE ` 줄을 읽어 최신순으로 돌려주고, 새 줄을 `runs/autoscale/<날짜>.jsonl`에 덧붙인다(중복은 `t`+`action`으로 건너뜀). 준비 시간: up 결정 뒤 `ready == desired`가 된 첫 시각과의 차를 `ready_after_s`로 채운다(콘솔이 파드 목록으로 계산).
- [ ] **Step 1: 실패하는 테스트** — `test_tick_disabled_returns_none`, `test_tick_scales_up_via_k8s`(가짜 K8s·Prom), `test_tick_holds_on_prom_error`(출력에 `AUTOSCALE` 줄과 "오류"), `test_tick_logs_hold_only_on_reason_change`, `test_ops_serve_role_allows_scale`(렌더된 Role rules), `test_autoscale_toggle_patches_configmap`, `test_decisions_parsed_and_appended_once`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_autoscaler_loop.py tests/test_gateway_manifests.py tests/test_ui_ops.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 전체 테스트**
- [ ] **Step 5: 커밋** — `git commit -m "Run autoscale loop in ops serve and show decisions in the console"`

### Task 10: 콘솔 서버의 원격 접속과 운영자 토큰

데스크탑 앱(Task 11)은 어느 OS에서든 네트워크로 콘솔 서버에 붙는다. 그래서 콘솔 서버가 "127.0.0.1에서만" 말고 "토큰을 가진 앱"도 받게 한다. 브라우저로 127.0.0.1에서 여는 기존 방식은 그대로 둔다.

**Files:**
- Create: `src/ttalkkak/ui/auth.py`
- Modify: `src/ttalkkak/ui/app.py` (`_LocalOnly` 옆에 토큰 미들웨어, CORS), `src/ttalkkak/ops_cli.py` (`ops ui --remote`, `ops token new|show|revoke`)
- Test: `tests/test_ui_auth.py`

**Interfaces:**
- Produces:
  - 토큰 파일 `~/.ttalkkak/console-token`(권한 0600, 기존 `client.config.write_private` 사용). `new_token() -> str`(`ttk_op_` + 32바이트 urlsafe), `load_token(path) -> str | None`, `check(header: str | None, token: str) -> bool`(상수 시간 비교)
  - `ops ui --remote`: `0.0.0.0`에 열고, `/api/*`는 `Authorization: Bearer <token>`이 있어야 한다(없거나 틀리면 401 `{"error":"운영자 토큰이 필요합니다"}`). Host 검사는 `--remote`에서는 끈다(토큰이 경계). 토큰 파일이 없으면 시작하지 않고 "`ttalkkak ops token new`로 먼저 만드세요" 오류.
  - CORS: `--remote`일 때 Origin `tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`만 허용, `Authorization`·`Content-Type` 헤더 허용.
  - `GET /api/ping`(토큰 필요) → `{"product":"ttalkkak","version":<패키지 버전>,"chip":<칩 이름>,"max_replicas":<n>}` — 앱의 연결 확인용.
- [ ] **Step 1: 실패하는 테스트** — `test_remote_requires_token`(토큰 없음 401, 틀림 401, 맞음 200), `test_local_mode_unchanged`(토큰 없이 127.0.0.1 Host로 200), `test_cors_only_tauri_origins`, `test_token_file_private_mode`(0600), `test_remote_without_token_file_refuses`, `test_ping_payload`.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_ui_auth.py -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 전체 테스트**
- [ ] **Step 5: 커밋** — `git commit -m "Let the console accept a desktop app over the network with an operator token"`

### Task 11: 데스크탑 앱 (Tauri) — 정식 앱 수준 화면과 실시간 탭

사용자 결정(2026-11-13): 브라우저 화면이 아니라 설치하는 데스크탑 앱. 운영자 컴퓨터의 OS를 모르므로 macOS·Windows·Linux를 모두 지원한다. (판단: Tauri v2. Electron보다 앱이 작고, 맥에 Rust가 이미 있으며, 화면은 지금처럼 빌드 도구 없는 정적 HTML/JS로 둘 수 있어 Node 빌드가 필요 없다.)

**Files:**
- Create: `desktop/src-tauri/{Cargo.toml,tauri.conf.json,build.rs,src/main.rs,capabilities/default.json,icons/*}`, `desktop/web/{index.html,app.css,app.js,connect.html}`
- Modify: `src/ttalkkak/ui/static/index.html` (브라우저 모드도 같은 `desktop/web` 파일을 쓰도록 `app.py`의 정적 경로를 `desktop/web`로 옮기거나 빌드 시 복사 — 판단: 패키지 데이터로 복사하는 `scripts/sync_web.py`와 테스트 하나)
- Test: `tests/test_ui_app.py` (정적 파일이 같은지), `desktop/src-tauri`의 `cargo test`(설정 저장 함수), 스크린샷 `docs/results/project2/app/*.png`

**Interfaces:**
- Consumes: Task 6·7·9의 `/api/*`, Task 10 `/api/ping`과 토큰
- Produces:
  - Rust 명령(`#[tauri::command]`): `save_connection(url: String, token: String) -> Result<(), String>`(OS 설정 폴더의 `connection.json`, 권한 0600; 토큰은 OS 키체인에 `keyring` 크레이트로, 실패하면 파일), `load_connection() -> Option<Connection>`, `forget_connection()`
  - 화면 JS는 모든 요청을 `api(path, opts)` 한 곳으로 보내고, 앱 안에서는 저장된 주소와 `Authorization` 헤더를, 브라우저(127.0.0.1)에서는 같은 출처를 쓴다.

- [ ] **Step 1: frontend-design 스킬을 불러 디자인 방향을 정한다.** 기준: 데스크탑 앱처럼 — 왼쪽 사이드바 탐색(아이콘+이름: 실시간, 개요, 원가, 테넌트, 사용량, 서빙 실행), 위쪽 얇은 상태 줄(연결된 서버, 칩 배지 "GPU 한 장 · 최대 복제본 N개", 연결 상태 점), 촘촘한 밀도, 운영체제 기본 글꼴, 어두운/밝은 테마는 OS 설정을 따름. 색·간격·글꼴 토큰을 `:root`에 정의하고 상태 색은 아이콘·글자와 함께만.
- [ ] **Step 2: Tauri 골격** — `cargo install tauri-cli --version "^2" --locked`, `desktop/src-tauri` 생성, 창 1280×800 최소 1024×700, 앱 이름 "ttalkkak 콘솔", 번들 식별자 `io.github.thisish3.ttalkkak`, 네이티브 메뉴(앱: 정보·종료 / 연결: 서버 바꾸기·연결 끊기 / 보기: 새로 고침·전체 화면 / 도움말: 문서 열기), 창 크기·위치 기억. `capabilities`는 HTTP 요청을 저장된 서버 주소로만.
- [ ] **Step 3: 연결 화면** — 처음 실행하면 서버 주소와 운영자 토큰 입력 → `/api/ping`으로 확인 → 저장. 실패하면 이유("주소에 닿지 않음", "토큰이 틀림 (401)", "ttalkkak 서버가 아님")를 그대로 보여 준다.
- [ ] **Step 4: 공통 컴포넌트와 기존 탭 이전** — 사이드바, 카드, 표(고정 헤더, 숫자 오른쪽 정렬), 버튼(주·보조·위험), 배지, 확인 모달(무엇을 얼마나 할지 문장으로), 토스트, 빈 상태, 로딩 스켈레톤, 오류 상자. 기존 5개 탭을 옮긴다(기능 변경 없음).
- [ ] **Step 5: 실시간 화면** — `docs/specs/mockups/live-capacity.html` 배치: 위쪽 숫자 6개, 용량 카드 3개(용량 사용률 원형 / 최근 1시간 선 그래프와 최대·평균·90 % 넘은 횟수 / 용량 부족 신호), 고객별 한도 소진률 막대, 복제본 표(상태, 처리 중/대기, KV, 적중률, GPU, "끄기"), 권장 조치(버튼), 자동 증감 스위치와 결정 기록, 부하 시험 카드(최대 사용자, 자동 증감 켜기/끄기, 시작, 진행 단계, 시험 기록별 "동시 사용자 대 첫 글자 p99" 겹친 선 그래프). `needs_loadtest`면 원형 대신 "부하 시험을 먼저 돌리세요"와 시험 카드로 가는 버튼. `errors` 섹션은 그 카드만 오류 상자. 2초 갱신, 창이 가려지면 멈춤, 실패 시 마지막 값 유지 + 상태 줄 "연결 끊김".
- [ ] **Step 6: 앱 알림** — 자동 증감 결정(up/down)과 거절(429) 급증 시 OS 알림(`tauri-plugin-notification`). 설정에서 끌 수 있음.
- [ ] **Step 7: 확인** — 맥에서 `cargo tauri dev`로 띄워 연결 화면, 모든 탭, 오류 상태(서버 꺼짐), 정상 상태(가짜 Prometheus 응답을 내는 테스트 서버)를 스크린샷. `cargo tauri build`로 `.dmg`가 만들어지는지 확인. `.venv/bin/python -m pytest -q`와 `cargo test` 통과.
- [ ] **Step 8: 커밋** — `git commit -m "Add the ttalkkak desktop console app"`

### Task 12: 세 OS 설치 파일 빌드 (GitHub Actions)

**Files:**
- Create: `.github/workflows/desktop.yml`

- [ ] **Step 1: 워크플로 작성** — `tauri-apps/tauri-action`으로 `macos-latest`(.dmg, Apple Silicon·Intel), `windows-latest`(.msi), `ubuntu-22.04`(.AppImage, .deb) 행렬 빌드. 트리거는 `desktop/**` 변경 push와 수동 실행. 산출물은 Actions artifact로 올린다(릴리스 자동 공개는 하지 않음). 서명은 하지 않는다: 맥은 "확인되지 않은 개발자" 경고가 뜨고 README에 여는 방법을 적는다. (판단: 서명 인증서는 비용이 들고 이번 범위가 아님)
- [ ] **Step 2: push 후 세 OS 빌드가 초록인지 확인** — `GH_CONFIG_DIR=~/.gh gh run watch`. 실패하면 로그를 읽고 고친다.
- [ ] **Step 3: 커밋** — `git commit -m "Build desktop installers for macOS, Windows and Linux"`

### Task 13: 데스크탑 지시문과 README

**Files:**
- Create: `docs/desktop/project2-desktop.md`
- Modify: `README.md` (기능 표의 "몰려도 버티기" 행, 칩 설정 파일 설명)

- [ ] **Step 1: 지시문 작성** — 순서: (1) `project2` 브랜치, (2) spike 결과 확인(`docs/results/spike-replicas/`), (3) NVIDIA device plugin time-slicing 설정과 확인, 안 되면 `--gpu-share runtime`, (4) `ops export --target gateway --max-replicas <n> --autoscale` 후 배포, 한 GPU에 몇 개까지 뜨는지 확인하고 결과 문서에 적기, (5) Prometheus 포트 포워딩과 `ops ui --remote --prometheus-url`, `ttalkkak ops token new`, 맥의 데스크탑 앱에서 tailnet 주소로 연결, (6) Task 6 질의 상수의 지표 이름을 `curl <vllm>/metrics`와 대조, 다르면 고치고 커밋, 게이트웨이 429·고객별 토큰을 접근 로그로 읽는 것이 맞는지 확인, (7) 자동 증감 끄고 복제본 1개·3개 부하 시험, (8) 시험 중 복제본 하나 끄기, (9) 자동 증감 켜고 동시 사용자 1 → 20 → 1 (부하 시험 두 번 + 대기), 결정 기록과 준비 시간·첫 글자 p99를 `docs/results/project2/`에, (10) 각 단계 화면 스크린샷. 규칙: 추측 금지("원인 미확인"), 계획과 다르게 한 결정은 `docs/project2-ledger.md`에 R1부터.
- [ ] **Step 2: README 갱신** — "몰려도 버티기" 행을 "구현됨 / 실기 검증 아직"으로, 칩 설정 파일 절 추가(`chips/*.yaml`로 칩 추가, 예시 파일 위치), 데스크탑 앱 설치와 연결 방법(서명 없는 맥 앱 여는 법 포함).
- [ ] **Step 3: 커밋하고 push** — `git push -u origin project2`

### Task 14: (데스크탑) 실기 검증

데스크탑 Claude가 `docs/desktop/project2-desktop.md`를 따라 수행한다. 맥 세션은 결과를 받아 README "실기 검증" 칸과 스펙 2절 표의 결론(3개 vs 1개)을 채운다. 스펙 5절의 기준 6개가 모두 결과 문서에 근거로 남아야 끝난다.
