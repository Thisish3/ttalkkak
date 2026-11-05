# ttalkkak 3단계 (K8s 매니페스트 출력) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `ttalkkak export --run <id> --target k8s|kserve|llmd|dynamo --out <dir>`가 선택된 후보의 설정을 대상 플랫폼 매니페스트로 쓴다.

**Architecture:** `export/common.py`가 run을 읽어 `RunBundle`(선택 후보, vendor, run id)을 만들고, 엔진 인자는 2단계 `ADAPTERS[engine].build_args`에서 가져온다. 대상마다 `render(bundle, image=None) -> dict[파일명, YAML 문자열]` 하나. 파일 쓰기와 덮어쓰기 검사는 `export/__init__.py`의 `write_export`만 한다.

**Tech Stack:** 1·2단계와 같음 + PyYAML(`yaml.safe_dump`).

**Spec:** `docs/specs/2026-11-06-ttalkkak-stage3-design.md`

## Global Constraints

- 1·2단계 제약 유지. 이미지: k8s는 `Platform.image(engine)`, kserve는 `ghcr.io/llm-d/llm-d-cuda:v0.9.0` / `ghcr.io/llm-d/llm-d-rocm:v0.9.0`, dynamo는 `--image` 필수(기본값 없음).
- llm-d 원격 참조는 `?ref=v1.0.0`, 경로는 `guides/recipes/modelserver/...`.
- Secret 이름: k8s·kserve `hf-token`, llmd `llm-d-hf-token`, dynamo `hf-token-secret`. 토큰 값은 절대 넣지 않는다.
- GPU 리소스: vendor `amd` → `amd.com/gpu`, 그 외 `nvidia.com/gpu`, 값 `"1"`.
- 오류 종료 코드 2.

## Review Focus

1. **선택 후보가 없는 run, 미완료 run** → 파일을 하나도 쓰지 않고 명확한 오류.
2. **`--out`에 기존 파일** → 덮어쓰지 않음(`--force`일 때만).
3. **SGLang run + kserve, AMD + dynamo** → 지원 범위를 말하는 오류.
4. **엔진 인자 변환 누락**: kserve·llmd에 `--model/--host/--port` 중복, dynamo에 엔진 파서 플래그가 남음 → 테스트로 고정.
5. **HF 토큰 값 유출** → 생성물 어디에도 환경변수 값이 들어가지 않음(Secret 참조만).

## Tasks

### Task 1: RunBundle과 인자 헬퍼

**Files:** Create `src/ttalkkak/export/__init__.py`, `src/ttalkkak/export/common.py`, `tests/test_export_common.py`, `tests/fixtures/runs/{vllm-nvidia,sglang-nvidia,vllm-amd}/{probe,plan,pick}.json`

**Produces:** `RunBundle(run_id: str, candidate: Candidate, vendor: str)`, `load_run(run_dir: Path) -> RunBundle`, `engine_args(c) -> list[str]`(어댑터 인자에서 실행 명령·`--model/--model-path`·`--host`·`--port`와 값 제거), `gpu_key(vendor) -> str`, `dump(docs: list[dict]) -> str`.

- [ ] 실패하는 테스트: 미완료 run(pick.json 없음) → `TtalkkakError` exit 2; 선택 없음 → 오류; `engine_args`가 vLLM/SGLang 모두에서 모델·host·port를 빼고 `--kv-cache-dtype`은 남김; `gpu_key("amd") == "amd.com/gpu"`.
- [ ] 구현 → 통과 → 커밋 "Add run loading for manifest export".

### Task 2: 대상 4개 렌더러

**Files:** Create `src/ttalkkak/export/{k8s,kserve,llmd,dynamo}.py`, `tests/test_export_targets.py`

**Produces:** 각 모듈 `render(b: RunBundle, image: str | None = None) -> dict[str, str]`.

- [ ] 실패하는 테스트(각 target을 fixture run으로 렌더 후 `yaml.safe_load_all`로 단언):
  - k8s: `Deployment`+`Service`, 이미지 `vllm/vllm-openai:v0.33.0`, args에 `--model`, 컨테이너 포트 8000(SGLang은 30000), readiness `/health`, `nvidia.com/gpu: "1"`, Secret `hf-token`.
  - kserve: `serving.kserve.io/v1alpha2` `LLMInferenceService`, `spec.model.uri == "hf://<id>"`, env `VLLM_ADDITIONAL_ARGS`에 `--kv-cache-dtype`는 있고 `--model`·`--port`는 없음; SGLang run → 오류; AMD run → `ghcr.io/llm-d/llm-d-rocm:v0.9.0`, `amd.com/gpu`.
  - llmd: `kustomization.yaml`의 resources/components가 `?ref=v1.0.0`과 `guides/recipes/...` 경로, AMD+SGLang은 `amd-sglang/rocm720-mi30x`; `patch.yaml`은 `Deployment decode`, 컨테이너 `modelserver`, vLLM command `["vllm","serve"]`와 args[0]이 모델, SGLang은 `--port=8000`.
  - dynamo: `nvidia.com/v1beta1` `DynamoGraphDeployment`, frontend+worker, worker command `python3 -m dynamo.vllm`, args에 `--tool-call-parser` 없음·`--dyn-tool-call-parser` 있음; `image=None` → 오류; AMD → 오류.
  - 공통: 렌더 결과 문자열에 테스트가 설정한 `HF_TOKEN` 값이 없음.
- [ ] 구현 → 통과 → 커밋 "Render k8s, KServe, llm-d and Dynamo manifests".

### Task 3: write_export와 CLI

**Files:** Modify `src/ttalkkak/export/__init__.py`, `src/ttalkkak/cli.py`; Create `tests/test_export_cli.py`

**Produces:** `TARGETS = {"k8s": ..., "kserve": ..., "llmd": ..., "dynamo": ...}`, `write_export(run_dir, target, out, force=False, image=None) -> list[Path]`(파일 + `README.md`: 적용 명령, Secret 생성 명령 `kubectl create secret generic <name> --from-literal=HF_TOKEN=$HF_TOKEN`, run id), CLI `ttalkkak export`.

- [ ] 실패하는 테스트: 정상 쓰기 목록; 기존 파일 → 오류, `--force` → 덮어씀; 알 수 없는 target → 오류; CLI 종료 코드 0/2(typer `CliRunner`).
- [ ] 구현 → 통과 → 커밋 "Add ttalkkak export command".

### Task 4: 실제 클러스터 적용 (사람이 실행)

- [ ] kind 또는 GPU 노드 클러스터에서 `ttalkkak export --target k8s` 결과를 `kubectl apply -f`, `kubectl kustomize <llmd out>`이 원격 base를 받아 빌드되는지 확인. 결과를 `docs/notes/`에 기록.
