# ttalkkak 2단계 (SGLang + AMD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ttalkkak up`이 vLLM 후보 2개와 SGLang 후보 1개를 한 번에 하나씩 띄워 같은 검증으로 고르고, NVIDIA와 AMD(ROCm) 양쪽에서 같은 흐름이 돈다.

**Architecture:** 후보 인자는 엔진 중립 이름만 쓰고, 엔진 플래그 변환·적용값 수집·정규화는 `EngineAdapter`(vLLM, SGLang)가 맡는다. 이미지와 GPU 플래그는 vendor별 `Platform`이 맡는다. verify·pick·report는 엔진을 모른다.

**Tech Stack:** 1단계와 같음. 이미지 `vllm/vllm-openai:v0.33.0`, `vllm/vllm-openai-rocm:v0.33.0`, `lmsysorg/sglang:v0.5.23`, `lmsysorg/sglang:v0.5.23-rocm720-mi30x`.

**Spec:** `docs/specs/2026-11-06-ttalkkak-stage2-design.md` (1단계 스펙 `docs/specs/2026-11-05-ttalkkak-design.md`도 유효)

## Global Constraints

- 1단계 Global Constraints 전부 유지(Python 3.12, uv, 고정 버전, 실패 분류 5종, 커밋 author/트레일러, push는 사용자 승인 후).
- 엔진 중립 인자 이름: `max_model_len`, `kv_cache_dtype`(`auto` | `fp8`), `gpu_memory_utilization`, `tool_call_parser`, `reasoning_parser`, `enable_auto_tool_choice`(vLLM 후보만).
- 후보 id: `c0`(기준선 전용, vLLM), `c1` vLLM auto, `c2` vLLM fp8, `c3` SGLang.
- SGLang 컨테이너 포트 30000, vLLM 8000.
- AMD GPU 플래그: `--device=/dev/kfd --device=/dev/dri --group-add video --group-add render --security-opt seccomp=unconfined`.
- 동률 규칙(스펙 2단계 5절): 출력 tok/s 동률이면 실측 `kv_cache_tokens`가 큰 후보, 그것도 같으면 앞선 후보.

## Review Focus

1. **SGLang `/server_info`가 404이거나 JSON이 아님**(구버전·다른 빌드) → 로그의 `context_len=`, `max_total_num_tokens=`로 대체, 둘 다 없으면 `unknown`. traceback 없음. (Task 4에 테스트)
2. **SGLang이 KV dtype을 `fp8_e4m3`로 보고** → `compare_applied`가 요청 `fp8`과 일치로 본다(정규화). (Task 4에 테스트)
3. **한 엔진에만 파서가 없는 모델**(예: 표에 sglang 항목이 빠짐) → 그 엔진 후보만 blocker, 나머지 후보는 정상. (Task 5에 테스트)
4. **AMD 장비에서 `nvidia-smi`가 없음** → `amd-smi`로 감지하고 `--gpus`를 붙이지 않는다. 둘 다 없으면 1단계와 같은 오류. (Task 3, 6에 테스트)
5. **SGLang 후보가 기준선 없이 fp8로만 가능** → `requires_accuracy` 후보는 vLLM c0/c1 기준선으로 판정하고, 기준선이 없으면 "기준 정확도 없음"으로 탈락. (Task 7에 테스트)

---

## File Structure

```
src/ttalkkak/
├── schemas.py              # GpuInfo.vendor/arch, Candidate.engine, Launched.collected
├── platform.py             # NEW: Platform(vendor).image(engine), gpu_flags()
├── engines/base.py         # EngineAdapter에 container_port, collect, normalize 반영
├── engines/vllm.py         # collect(), read_applied(logs, collected)
├── engines/sglang.py       # NEW
├── engines/__init__.py     # ADAPTERS = {"vllm": ..., "sglang": ...}
├── probe/gpu.py            # amd-smi 파싱 추가
├── plan/parsers.py         # parsers_for(family, engine)
├── data/parsers.yaml       # 엔진별 표
├── plan/candidates.py      # c3 추가
├── launch/docker.py        # platform 인자, adapter.collect 사용
├── pick.py                 # 동률 규칙
├── report.py               # 엔진 열
└── pipeline.py             # 엔진별 어댑터, Platform 전달
```

---

### Task 1: 스키마와 어댑터 인터페이스 정리 (동작 변화 없음)

**Files:**
- Modify: `src/ttalkkak/schemas.py`, `src/ttalkkak/engines/base.py`, `src/ttalkkak/engines/vllm.py`, `src/ttalkkak/launch/docker.py`, `src/ttalkkak/pipeline.py`, `tests/test_vllm_adapter.py`, `tests/test_launch.py`, `tests/test_pipeline.py`, `tests/test_report.py`
- Create: `src/ttalkkak/engines/__init__.py` 내용(현재 빈 파일)

**Interfaces:**
- Produces:
  - `GpuInfo(name, memory_mib, compute_capability, vendor: Literal["nvidia","amd"]="nvidia", arch: str="")`
  - `Candidate.engine: Literal["vllm","sglang"]`
  - `Launched.collected: dict` (기존 `metrics`, `models_json` 필드 삭제)
  - `EngineAdapter`: `name: str`, `container_port: int`, `build_args(c) -> ContainerSpec`, `collect(base_url, client) -> dict`, `read_applied(logs: str, collected: dict) -> AppliedConfig`, `health(base_url, client) -> bool`
  - `VllmAdapter.collect` → `{"models": <dict>, "metrics": <str>}` (실패한 키는 생략)
  - `ttalkkak.engines.ADAPTERS: dict[str, EngineAdapter]` (이 Task에서는 `{"vllm": VllmAdapter()}`)

- [ ] **Step 1: 실패하는 테스트로 새 인터페이스 고정**

`tests/test_vllm_adapter.py`의 `test_read_applied_from_logs_metrics_and_models`, `test_missing_values_are_unknown`을 새 시그니처로 바꾸고 `collect` 테스트를 추가한다.

```python
def test_read_applied_from_logs_metrics_and_models():
    a = A.read_applied(
        (FIX / "startup_sample.log").read_text(),
        {"metrics": (FIX / "metrics_sample.txt").read_text(),
         "models": {"data": [{"id": "m", "max_model_len": 16384}]}},
    )
    v = a.values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_dtype"].value == "fp8"
    assert v["kv_cache_tokens"].value == 71632
    assert v["attention_backend"].value == "FLASH_ATTN"
    assert v["tool_call_parser"].value == "hermes"
    assert "cache_config_info" in v["kv_cache_dtype"].evidence


def test_missing_values_are_unknown():
    a = A.read_applied("", {})
    assert a.values["attention_backend"].value is None
    assert a.values["attention_backend"].evidence == "unknown"


def test_collect_gets_models_and_metrics_and_skips_failures():
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(500)

    got = A.collect("http://x", httpx.Client(transport=httpx.MockTransport(handler)))
    assert got == {"models": {"data": []}}
    assert A.container_port == 8000
```

`tests/test_launch.py`의 `test_launch_ready_collects_logs_and_models`에서 `r.models_json["data"][0]["max_model_len"]`를 `r.collected["models"]["data"][0]["max_model_len"]`로 바꾼다. `tests/test_pipeline.py`의 `Launched(... models_json={}, logs="", metrics="")` 두 곳을 `Launched(... collected={}, logs="")`로 바꾼다.

Run: `uv run pytest -q` → Expected: FAIL (`read_applied` 인자 수, `collect` 없음, `collected` 필드 없음).

- [ ] **Step 2: 구현**

`schemas.py`:
```python
class GpuInfo(BaseModel):
    name: str
    memory_mib: int
    compute_capability: str
    vendor: Literal["nvidia", "amd"] = "nvidia"
    arch: str = ""
```
`Candidate.engine: Literal["vllm", "sglang"]`. `Launched`에서 `metrics: str`, `models_json: dict`를 지우고 `collected: dict = Field(default_factory=dict)`.

`engines/base.py`의 Protocol:
```python
class EngineAdapter(Protocol):
    name: str
    container_port: int

    def build_args(self, c: Candidate) -> ContainerSpec: ...

    def collect(self, base_url: str, client: httpx.Client) -> dict: ...

    def read_applied(self, logs: str, collected: dict) -> AppliedConfig: ...

    def health(self, base_url: str, client: httpx.Client) -> bool: ...
```

`engines/vllm.py`: 클래스 속성 `container_port = 8000`, 그리고
```python
    def collect(self, base_url: str, client: httpx.Client) -> dict:
        out: dict = {}
        try:
            r = client.get(f"{base_url}/v1/models", timeout=10)
            if r.status_code == 200:
                out["models"] = r.json()
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = client.get(f"{base_url}/metrics", timeout=10)
            if r.status_code == 200:
                out["metrics"] = r.text
        except httpx.HTTPError:
            pass
        return out
```
`read_applied(self, logs, collected)`의 본문에서 `metrics`는 `collected.get("metrics", "")`, `models`는 `collected.get("models", {})`로 읽는다(나머지 로직 그대로).

`engines/__init__.py`:
```python
from ttalkkak.engines.vllm import VllmAdapter

ADAPTERS = {"vllm": VllmAdapter()}
```

`launch/docker.py` ready 분기:
```python
        if adapter.health(base, client):
            return Launched(candidate_id=c.id, status="ready", base_url=base,
                            container=cid, logs=logs,
                            collected=adapter.collect(base, client))
```

`pipeline.py`: `adapter.read_applied(item.logs, item.collected)`.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` → Expected: 전부 통과(1단계 86개 + 신규 1개), ruff 깨끗.

- [ ] **Step 4: 커밋**

```bash
git add -A src tests
git commit -m "Move engine endpoint collection into adapters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Platform (vendor별 이미지와 GPU 플래그)

**Files:**
- Create: `src/ttalkkak/platform.py`, `tests/test_platform.py`
- Modify: `src/ttalkkak/launch/docker.py`, `src/ttalkkak/engines/vllm.py`, `tests/test_launch.py`

**Interfaces:**
- Consumes: `ContainerSpec`, `GpuInfo.vendor`
- Produces:
  - `Platform(vendor: Literal["nvidia","amd"])`, `.image(engine: str) -> str | None`, `.gpu_flags() -> list[str]`, `Platform.from_gpu(g: GpuInfo | None) -> Platform`
  - `launch(c, adapter, platform: Platform = Platform(vendor="nvidia"), run=..., client=..., wait_s=..., sleep=...)`
  - `ContainerSpec.image`는 어댑터가 비워 두고(`""`), launch가 `platform.image(adapter.name)`로 채운다.

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_platform.py
from ttalkkak.platform import Platform
from ttalkkak.schemas import GpuInfo


def test_nvidia_images_and_flags():
    p = Platform(vendor="nvidia")
    assert p.image("vllm") == "vllm/vllm-openai:v0.33.0"
    assert p.image("sglang") == "lmsysorg/sglang:v0.5.23"
    assert p.gpu_flags() == ["--gpus", "all"]


def test_amd_images_and_flags_have_no_gpus_flag():
    p = Platform(vendor="amd")
    assert p.image("vllm") == "vllm/vllm-openai-rocm:v0.33.0"
    assert p.image("sglang") == "lmsysorg/sglang:v0.5.23-rocm720-mi30x"
    flags = p.gpu_flags()
    assert "--gpus" not in flags
    assert "--device=/dev/kfd" in flags and "--device=/dev/dri" in flags


def test_unknown_engine_has_no_image():
    assert Platform(vendor="nvidia").image("trtllm") is None


def test_from_gpu_defaults_to_nvidia():
    g = GpuInfo(name="MI300X", memory_mib=196608, compute_capability="", vendor="amd")
    assert Platform.from_gpu(g).vendor == "amd"
    assert Platform.from_gpu(None).vendor == "nvidia"
```

`tests/test_launch.py`에 추가:
```python
def test_amd_platform_uses_rocm_image_and_devices():
    from ttalkkak.platform import Platform

    fake = FakeDocker()
    launch(C, VllmAdapter(), platform=Platform(vendor="amd"), run=fake,
           client=_client(200), wait_s=5, sleep=lambda s: None)
    run_cmd = next(c for c in fake.calls if c[:2] == ["docker", "run"])
    assert "--gpus" not in run_cmd and "--device=/dev/kfd" in run_cmd
    assert "vllm/vllm-openai-rocm:v0.33.0" in run_cmd
    assert fake.calls[0] == ["docker", "pull", "vllm/vllm-openai-rocm:v0.33.0"]
```

Run: `uv run pytest tests/test_platform.py tests/test_launch.py -q` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/platform.py
from typing import Literal

from pydantic import BaseModel

from ttalkkak.schemas import GpuInfo

_IMAGES = {
    "nvidia": {"vllm": "vllm/vllm-openai:v0.33.0",
               "sglang": "lmsysorg/sglang:v0.5.23"},
    "amd": {"vllm": "vllm/vllm-openai-rocm:v0.33.0",
            "sglang": "lmsysorg/sglang:v0.5.23-rocm720-mi30x"},
}
_FLAGS = {
    "nvidia": ["--gpus", "all"],
    "amd": ["--device=/dev/kfd", "--device=/dev/dri", "--group-add", "video",
            "--group-add", "render", "--security-opt", "seccomp=unconfined"],
}


class Platform(BaseModel):
    vendor: Literal["nvidia", "amd"]

    def image(self, engine: str) -> str | None:
        return _IMAGES[self.vendor].get(engine)

    def gpu_flags(self) -> list[str]:
        return list(_FLAGS[self.vendor])

    @classmethod
    def from_gpu(cls, g: GpuInfo | None) -> "Platform":
        return cls(vendor=g.vendor if g else "nvidia")
```

`engines/vllm.py`: `IMAGE` 상수 삭제, `build_args`가 `ContainerSpec(image="", args=args)`를 돌려준다. `tests/test_vllm_adapter.py`의 `assert spec.image == "vllm/vllm-openai:v0.33.0"` 줄은 삭제한다(이미지는 Platform 책임).

`launch/docker.py`: 시그니처에 `platform: Platform = Platform(vendor="nvidia")` 추가(두 번째 인자 다음, 키워드로도 받음). 시작 부분에서
```python
    spec = adapter.build_args(c)
    image = platform.image(adapter.name)
    if image is None:
        return _failed(c, f"no image for {adapter.name} on {platform.vendor}", False,
                       kind="bad_arg")
```
이후 `spec.image` 대신 `image`를 쓰고, `"--gpus", "all"` 대신 `*platform.gpu_flags()`, 포트는 `f"{port}:{adapter.container_port}"`.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` → Expected: 전부 통과.

- [ ] **Step 4: 커밋**

```bash
git add -A src tests
git commit -m "Add vendor platform for images and GPU flags

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: AMD GPU 감지 (amd-smi)

**Files:**
- Modify: `src/ttalkkak/probe/gpu.py`, `tests/test_probe.py`
- Create: `tests/fixtures/amd/amd_smi_static.json`

**Interfaces:**
- Produces: `parse_amd_smi(json_text: str) -> GpuInfo`, `detect_gpu(run=subprocess.run) -> GpuInfo | None` (nvidia-smi 실패 시 `amd-smi static --asic --vram --json` 시도)

- [ ] **Step 1: fixture (SYNTHETIC)**

```json
[{"_note": "SYNTHETIC: amd-smi static --asic --vram --json shape assumed; replace from MI300X (Task 8)",
  "gpu": 0,
  "asic": {"market_name": "AMD Instinct MI300X", "target_graphics_version": "gfx942"},
  "vram": {"type": "HBM", "size": {"value": 196592, "unit": "MB"}}}]
```

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_probe.py에 추가
import subprocess

from ttalkkak.probe.gpu import detect_gpu, parse_amd_smi


def test_parse_amd_smi():
    g = parse_amd_smi((FIX / "amd/amd_smi_static.json").read_text())
    assert g.vendor == "amd" and g.arch == "gfx942"
    assert g.name == "AMD Instinct MI300X" and g.memory_mib == 196592


def test_detect_falls_back_to_amd_smi():
    amd = (FIX / "amd/amd_smi_static.json").read_text()

    def run(cmd, **kw):
        if cmd[0] == "nvidia-smi":
            raise FileNotFoundError
        return subprocess.CompletedProcess(cmd, 0, stdout=amd, stderr="")

    g = detect_gpu(run)
    assert g is not None and g.vendor == "amd"


def test_no_gpu_tools_means_none():
    def run(cmd, **kw):
        raise FileNotFoundError

    assert detect_gpu(run) is None
```

Run: `uv run pytest tests/test_probe.py -q` → Expected: FAIL (`parse_amd_smi` 없음).

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/probe/gpu.py에 추가/수정
import json

AMD_QUERY = ["amd-smi", "static", "--asic", "--vram", "--json"]
_MB = {"MB": 1, "MiB": 1, "GB": 1024, "GiB": 1024}


def parse_amd_smi(json_text: str) -> GpuInfo:
    first = json.loads(json_text)[0]
    size = first["vram"]["size"]
    return GpuInfo(
        name=first["asic"]["market_name"],
        memory_mib=int(size["value"] * _MB.get(size.get("unit", "MB"), 1)),
        compute_capability="",
        vendor="amd",
        arch=first["asic"].get("target_graphics_version", ""),
    )


def detect_gpu(run=subprocess.run) -> GpuInfo | None:
    try:
        out = run(QUERY, capture_output=True, text=True, timeout=10, check=True)
        return parse_nvidia_smi(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        out = run(AMD_QUERY, capture_output=True, text=True, timeout=10, check=True)
        return parse_amd_smi(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, KeyError,
            IndexError):
        return None
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q` → Expected: 전부 통과.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/probe tests/test_probe.py tests/fixtures/amd
git commit -m "Detect AMD GPUs with amd-smi

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: SGLang 어댑터

**Files:**
- Create: `src/ttalkkak/engines/sglang.py`, `tests/test_sglang_adapter.py`, `tests/fixtures/sglang/server_info_sample.json`, `tests/fixtures/sglang/startup_sample.log`
- Modify: `src/ttalkkak/engines/__init__.py`

**Interfaces:**
- Consumes: `ContainerSpec`, `Candidate`, `AppliedConfig`, `AppliedValue`
- Produces: `SglangAdapter` (`name="sglang"`, `container_port=30000`), `ADAPTERS["sglang"]`
  - `read_applied`는 중립 이름·값으로 돌려준다: `max_model_len`, `kv_cache_dtype`(`fp8_e4m3`/`fp8_e5m2` → `fp8`, `bfloat16`/`bf16`/`auto` → `auto`), `kv_cache_tokens`, `tool_call_parser`, `reasoning_parser`, `attention_backend`

- [ ] **Step 1: fixture (SYNTHETIC)**

`tests/fixtures/sglang/server_info_sample.json`:
```json
{"_note": "SYNTHETIC: field names from sglang http_server.py server_info + ServerArgs; replace from a real run (Task 8)",
 "model_path": "Qwen/Qwen2.5-1.5B-Instruct", "context_length": 16384,
 "kv_cache_dtype": "fp8_e4m3", "tool_call_parser": "qwen25", "reasoning_parser": null,
 "attention_backend": "flashinfer", "mem_fraction_static": 0.9,
 "launch_command": "python -m sglang.launch_server --model-path Qwen/Qwen2.5-1.5B-Instruct",
 "internal_states": [{"max_total_num_tokens": 90112}]}
```
`tests/fixtures/sglang/startup_sample.log`:
```
# SYNTHETIC: log line format from sglang docs (hyperparameter_tuning.mdx); replace from a real run (Task 8)
[2026-11-06 10:00:00] max_total_num_tokens=90112, chunked_prefill_size=8192, max_prefill_tokens=16384, max_running_requests=4096, context_len=16384, available_gpu_mem=1.20 GB
```

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_sglang_adapter.py
import json
from pathlib import Path

import httpx

from ttalkkak.engines.sglang import SglangAdapter
from ttalkkak.schemas import Candidate
from ttalkkak.verify.applied import compare_applied

FIX = Path(__file__).parent / "fixtures/sglang"
A = SglangAdapter()
C = Candidate(id="c3", engine="sglang", model_id="Qwen/Qwen2.5-1.5B-Instruct",
              args={"max_model_len": 16384, "kv_cache_dtype": "fp8",
                    "gpu_memory_utilization": 0.9, "tool_call_parser": "qwen25"})


def test_build_args_translates_neutral_names():
    a = A.build_args(C).args
    assert a[:3] == ["python3", "-m", "sglang.launch_server"]
    assert a[3:5] == ["--model-path", "Qwen/Qwen2.5-1.5B-Instruct"]
    assert a[a.index("--context-length") + 1] == "16384"
    assert a[a.index("--kv-cache-dtype") + 1] == "fp8_e4m3"
    assert a[a.index("--mem-fraction-static") + 1] == "0.9"
    assert a[a.index("--tool-call-parser") + 1] == "qwen25"
    assert "--enable-metrics" in a and a[a.index("--port") + 1] == "30000"
    assert "--enable-auto-tool-choice" not in a


def test_read_applied_from_server_info_normalizes_fp8():
    info = json.loads((FIX / "server_info_sample.json").read_text())
    v = A.read_applied("", {"server_info": info}).values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_dtype"].value == "fp8"
    assert v["kv_cache_tokens"].value == 90112
    assert v["tool_call_parser"].value == "qwen25"
    assert "server_info" in v["kv_cache_dtype"].evidence
    assert compare_applied(C, A.read_applied("", {"server_info": info})) == []


def test_falls_back_to_log_without_server_info():
    log = (FIX / "startup_sample.log").read_text()
    v = A.read_applied(log, {}).values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_tokens"].value == 90112
    assert v["kv_cache_dtype"].value is None


def test_collect_tolerates_missing_server_info():
    def handler(req):
        if req.url.path == "/server_info":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={})

    got = A.collect("http://x", httpx.Client(transport=httpx.MockTransport(handler)))
    assert "server_info" not in got
```

Run: `uv run pytest tests/test_sglang_adapter.py -q` → Expected: FAIL (모듈 없음).

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/engines/sglang.py
import re

import httpx

from ttalkkak.engines.base import ContainerSpec
from ttalkkak.schemas import AppliedConfig, AppliedValue, Candidate

_FLAGS = {"max_model_len": "--context-length",
          "gpu_memory_utilization": "--mem-fraction-static",
          "tool_call_parser": "--tool-call-parser",
          "reasoning_parser": "--reasoning-parser"}
_KV_TO_ENGINE = {"auto": "auto", "fp8": "fp8_e4m3"}
_KV_FROM_ENGINE = {"fp8_e4m3": "fp8", "fp8_e5m2": "fp8", "auto": "auto",
                   "bf16": "auto", "bfloat16": "auto"}
_LOG_TOKENS = re.compile(r"max_total_num_tokens=(\d+)")
_LOG_CTX = re.compile(r"context_len=(\d+)")
_KEYS = ("max_model_len", "kv_cache_dtype", "kv_cache_tokens", "attention_backend",
         "tool_call_parser", "reasoning_parser")


def _find(pattern: re.Pattern, text: str) -> tuple[str, str] | None:
    for line in text.splitlines():
        if m := pattern.search(line):
            return m.group(1), line.strip()
    return None


class SglangAdapter:
    name = "sglang"
    container_port = 30000

    def build_args(self, c: Candidate) -> ContainerSpec:
        args = ["--model-path", c.model_id, "--host", "0.0.0.0",
                "--port", str(self.container_port), "--enable-metrics"]
        for key, val in c.args.items():
            if key == "kv_cache_dtype":
                args += ["--kv-cache-dtype", _KV_TO_ENGINE[str(val)]]
            elif key in _FLAGS and val not in (None, False):
                args += [_FLAGS[key], str(val)]
        return ContainerSpec(image="", args=["python3", "-m", "sglang.launch_server",
                                             *args])

    def collect(self, base_url: str, client: httpx.Client) -> dict:
        try:
            r = client.get(f"{base_url}/server_info", timeout=10)
            if r.status_code == 200:
                return {"server_info": r.json()}
        except (httpx.HTTPError, ValueError):
            pass
        return {}

    def read_applied(self, logs: str, collected: dict) -> AppliedConfig:
        v = {k: AppliedValue(value=None, evidence="unknown") for k in _KEYS}
        info = collected.get("server_info") or {}
        for key, field in (("max_model_len", "context_length"),
                           ("tool_call_parser", "tool_call_parser"),
                           ("reasoning_parser", "reasoning_parser"),
                           ("attention_backend", "attention_backend")):
            if info.get(field) is not None:
                v[key] = AppliedValue(value=info[field],
                                      evidence=f"/server_info {field}")
        if info.get("kv_cache_dtype") is not None:
            raw = str(info["kv_cache_dtype"])
            v["kv_cache_dtype"] = AppliedValue(
                value=_KV_FROM_ENGINE.get(raw, raw),
                evidence=f"/server_info kv_cache_dtype={raw}")
        states = info.get("internal_states") or []
        if states and states[0].get("max_total_num_tokens") is not None:
            v["kv_cache_tokens"] = AppliedValue(
                value=int(states[0]["max_total_num_tokens"]),
                evidence="/server_info internal_states[0].max_total_num_tokens")
        if v["kv_cache_tokens"].value is None and (hit := _find(_LOG_TOKENS, logs)):
            v["kv_cache_tokens"] = AppliedValue(value=int(hit[0]), evidence=hit[1])
        if v["max_model_len"].value is None and (hit := _find(_LOG_CTX, logs)):
            v["max_model_len"] = AppliedValue(value=int(hit[0]), evidence=hit[1])
        return AppliedConfig(values=v)

    def health(self, base_url: str, client: httpx.Client) -> bool:
        try:
            return client.get(f"{base_url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False
```

`build_args`의 `args`가 `python3 -m sglang.launch_server`로 시작하는 이유: SGLang 이미지는 vLLM 이미지와 달리 서버 entrypoint가 기본이 아니다. Task 8에서 실제 이미지 entrypoint를 확인해 다르면 고친다. 테스트는 이 형태(앞 3개가 실행 명령, 그다음 `--model-path`)를 고정한다.

`engines/__init__.py`:
```python
from ttalkkak.engines.sglang import SglangAdapter
from ttalkkak.engines.vllm import VllmAdapter

ADAPTERS = {"vllm": VllmAdapter(), "sglang": SglangAdapter()}
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` → Expected: 전부 통과.

- [ ] **Step 5: 커밋**

```bash
git add -A src tests
git commit -m "Add SGLang adapter with server_info-based applied config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 엔진별 파서 표

**Files:**
- Modify: `src/ttalkkak/data/parsers.yaml`, `src/ttalkkak/plan/parsers.py`, `src/ttalkkak/plan/candidates.py`, `tests/test_candidates.py`

**Interfaces:**
- Produces: `parsers_for(family: str, engine: str) -> ParserEntry | None`; `_parser_args(family, req, engine) -> tuple[dict, list[str]]` (SGLang에는 `enable_auto_tool_choice`를 넣지 않는다)

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_candidates.py에 추가
from ttalkkak.plan.parsers import parsers_for


def test_parser_table_is_per_engine():
    assert parsers_for("qwen2", "vllm").tool == "hermes"
    assert parsers_for("qwen2", "sglang").tool == "qwen25"
    assert parsers_for("qwen2", "trtllm") is None
```

Run: `uv run pytest tests/test_candidates.py -q` → Expected: FAIL (인자 개수).

- [ ] **Step 2: 구현**

`parsers.yaml`을 스펙 4.6 표로 교체(주석 첫 줄에 확인 출처: vLLM `vllm/tool_parsers/__init__.py`, SGLang `function_call/function_call_parser.py`, `parser/reasoning_parser.py`).

```python
# plan/parsers.py
@cache
def _table() -> dict[str, dict[str, ParserEntry]]:
    raw = yaml.safe_load(files("ttalkkak.data").joinpath("parsers.yaml").read_text())
    return {fam: {eng: ParserEntry.model_validate(e) for eng, e in engines.items()}
            for fam, engines in raw.items()}


def parsers_for(family: str, engine: str) -> ParserEntry | None:
    return _table().get(family, {}).get(engine)
```

`candidates.py`의 `_parser_args(family, req, engine)`: `entry = parsers_for(family, engine)`, tool이 있으면 vLLM일 때만 `enable_auto_tool_choice=True`를 추가. blocker 문장에 엔진 이름을 넣는다: `f"no tool parser known for {family} on {engine} (needed by {who})"`. `make_plan`은 vLLM 후보용으로 `_parser_args(m.family, req, "vllm")`를 부른다. 기존 테스트 `test_unknown_family_blocks_when_tools_needed`의 부분 문자열 `"no tool parser known for unknownlm"`은 그대로 통과한다.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q` → Expected: 전부 통과.

- [ ] **Step 4: 커밋**

```bash
git add -A src tests
git commit -m "Split parser table by engine

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: SGLang 후보 c3

**Files:**
- Modify: `src/ttalkkak/plan/candidates.py`, `tests/test_candidates.py`

**Interfaces:**
- Produces: `make_plan`이 c1, c2(vLLM) 다음에 c3(`engine="sglang"`)를 추가. c0 규칙은 그대로이고 c0은 맨 앞.

c3 규칙: auto KV로 `max_model_len = min(max_position, fit_auto) ≥ min_context`이면 `kv_cache_dtype="auto"`, 아니면 fp8로 같은 계산. 길이가 부족하면 blocker(1단계 문장 형식). fp8이면 `requires_accuracy=True`. SGLang 파서가 없으면 c3에만 blocker. AMD(`gpu.vendor == "amd"`)여도 같은 규칙.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_c3_is_sglang_with_auto_kv_when_it_fits():
    p = make_plan(ProbeResult(model=M, gpu=G), req(min_context=4096))
    c3 = next(c for c in p.candidates if c.id == "c3")
    assert c3.engine == "sglang" and c3.args["kv_cache_dtype"] == "auto"
    assert not c3.requires_accuracy and not c3.blockers


def test_c3_uses_fp8_when_auto_cannot_hold_context():
    from ttalkkak.plan.memory import max_context_fit

    need = max_context_fit(M, G, "auto") + 1
    p = make_plan(ProbeResult(model=M, gpu=G), req(min_context=need))
    c3 = next(c for c in p.candidates if c.id == "c3")
    assert c3.args["kv_cache_dtype"] == "fp8" and c3.requires_accuracy


def test_missing_sglang_parser_blocks_only_c3(monkeypatch):
    from ttalkkak.plan import candidates
    from ttalkkak.plan.parsers import ParserEntry

    def fake(family, engine):
        return ParserEntry(tool="hermes", reasoning=None) if engine == "vllm" else None

    monkeypatch.setattr(candidates, "parsers_for", fake)
    p = make_plan(ProbeResult(model=M, gpu=G), req(flags={"tool_calling": True}))
    by = {c.id: c for c in p.candidates}
    assert not by["c1"].blockers and any("sglang" in b for b in by["c3"].blockers)
    assert "enable_auto_tool_choice" not in by["c3"].args
```

Run: `uv run pytest tests/test_candidates.py -q` → Expected: FAIL (c3 없음).

- [ ] **Step 2: 구현**

`make_plan`에서 c1·c2 루프 뒤, c0 삽입 전에:
```python
    s_args, s_blockers = _parser_args(m.family, req, "sglang")
    kv = "auto" if min(m.max_position, max_context_fit(m, g, "auto")) >= max(
        req.min_context, 1) else "fp8"
    fit = max_context_fit(m, g, kv)
    length = min(m.max_position, fit)
    blockers = list(s_blockers)
    reasons = [f"SGLang KV {kv}: 들어가는 문맥 약 {fit} 토큰"]
    if length < max(req.min_context, 1):
        blockers.append(f"max_model_len {length} < 필요 {req.min_context}")
        who = ", ".join(req.sources.get("min_context", [])) or "요청"
        reasons.append(f"{who}의 min_context {req.min_context} 때문에 불가")
    out.append(Candidate(
        id="c3", engine="sglang", model_id=m.model_id,
        args={"kv_cache_dtype": kv, "max_model_len": length,
              "gpu_memory_utilization": 0.9} | s_args,
        predicted={"weight_gib": m.weight_bytes / 2**30, "kv_tokens_fit": float(fit)},
        reasons=reasons, blockers=blockers, requires_accuracy=(kv == "fp8")))
```
c0 판단의 `fp8_runnable`은 `out[1:]` 중 vLLM fp8 후보만 보던 것을 "c1 외 어떤 후보든 실행 가능"으로 유지한다(c3가 fp8로 가능할 때도 기준선이 필요). 오류 메시지의 "NVIDIA GPU"를 "GPU"로 바꾼다.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q` → Expected: 전부 통과. 1단계 테스트 중 후보 개수를 세는 곳이 있으면(예: `[c.args["kv_cache_dtype"] for c in p.candidates] == ["auto", "fp8"]`) vLLM 후보만 보도록 `if c.engine == "vllm"` 필터를 추가한다.

- [ ] **Step 4: 커밋**

```bash
git add -A src tests
git commit -m "Add an SGLang candidate to the plan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 파이프라인·선택·리포트를 엔진 무관으로

**Files:**
- Modify: `src/ttalkkak/pipeline.py`, `src/ttalkkak/pick.py`, `src/ttalkkak/report.py`, `src/ttalkkak/cli.py`, `tests/test_pipeline.py`, `tests/test_pick.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: `ADAPTERS` (Task 4), `Platform.from_gpu` (Task 2)
- Produces:
  - `Deps.launch(c, adapter, platform)` 세 인자로 호출
  - `pick(verdicts, baseline, launched_urls)`: 동률이면 `v.applied.values["kv_cache_tokens"].value`가 큰 것, 같으면 앞선 것
  - 리포트 예측 vs 실측 표 첫 열 뒤에 `엔진` 열

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_pick.py에 추가 (기존 test_tie_prefers_earlier_candidate는 kv 토큰이 같은 경우로 유지)
from ttalkkak.schemas import AppliedConfig, AppliedValue


def test_tie_prefers_more_kv_tokens():
    def with_kv(cid, kv):
        x = v(cid, 30)
        x.applied = AppliedConfig(values={
            "kv_cache_tokens": AppliedValue(value=kv, evidence="log")})
        return x

    p = pick([with_kv("c1", 1000), with_kv("c2", 2000)], baseline=None,
             launched_urls={"c1": "u1", "c2": "u2"})
    assert p.chosen == "c2"
```

```python
# tests/test_pipeline.py: Gpu.launch 시그니처를 (self, c, adapter, platform=None, **kw)로,
# deps()의 launch도 (c, adapter, platform=None, **kw)로 바꾼 뒤 추가
def test_sglang_candidate_runs_after_vllm_and_can_win(tmp_path):
    gpu = Gpu()
    seen = []
    orig = gpu.launch

    def launch(c, adapter, platform=None, **kw):
        seen.append((c.id, adapter.name))
        return orig(c, adapter, platform)

    def perf(url, model, out):
        return {"output_tok_s": 30.0 if "/c3/" in url else 10.0}

    d = gpu_deps(gpu, run_perf=perf)
    d.launch = launch
    _, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, d)
    assert seen[:3] == [("c1", "vllm"), ("c2", "vllm"), ("c3", "sglang")]
    assert seen[3] == ("c3", "sglang")  # winner relaunched
    assert p.chosen == "c3" and gpu.peak == 1


def test_amd_probe_passes_amd_platform(tmp_path):
    gpu = Gpu()
    vendors = []
    orig = gpu.launch

    def launch(c, adapter, platform=None, **kw):
        vendors.append(platform.vendor)
        return orig(c, adapter, platform)

    amd = G.model_copy(update={"vendor": "amd", "arch": "gfx942"})
    d = gpu_deps(gpu, probe=lambda m: ProbeResult(model=M, gpu=amd))
    d.launch = launch
    run_up(M.model_id, ["tooly"], tmp_path, SKILLS, d)
    assert vendors and set(vendors) == {"amd"}


def test_sglang_fp8_without_baseline_is_rejected(tmp_path, monkeypatch):
    from ttalkkak import pipeline
    from ttalkkak.schemas import Candidate, Plan, Requirements

    plan = Plan(requirements=Requirements(), candidates=[
        Candidate(id="c3", engine="sglang", model_id=M.model_id,
                  args={"kv_cache_dtype": "fp8"}, requires_accuracy=True)])
    monkeypatch.setattr(pipeline, "make_plan", lambda pr, req: plan)
    _, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, gpu_deps(Gpu()))
    assert p.chosen is None and p.rejected["c3"] == "기준 정확도 없음"


def test_sglang_auto_score_does_not_replace_vllm_baseline(tmp_path):
    def acc(url, model, out):
        return 0.9 if "/c3/" in url else 0.6

    _, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS,
                  gpu_deps(Gpu(), run_accuracy=acc))
    assert "c2" not in p.rejected  # c2 (vLLM fp8) judged against vLLM c1 = 0.6
```

`tests/test_report.py`에 `assert "| c1 | vllm | 8192 |" in md`로 기존 `"| c1 | 8192 |"` 단언을 바꾼다.

Run: `uv run pytest -q` → Expected: FAIL (동률 규칙, launch 인자, 엔진 열).

- [ ] **Step 2: 구현**

`pick.py`:
```python
def _kv(v: CandidateVerdict) -> float:
    got = v.applied.values.get("kv_cache_tokens")
    return float(got.value) if got and isinstance(got.value, int | float) else 0.0

    # in pick(): max() keeps the first of equal keys
    best = max(ok, key=lambda v: (v.perf.get("output_tok_s", 0.0), _kv(v)))
```

`pipeline.py`: 기준선은 `c.engine == "vllm" and c.args.get("kv_cache_dtype") == "auto"`인 후보의 점수만 쓴다(스펙 4.7). `from ttalkkak.engines import ADAPTERS`, `from ttalkkak.platform import Platform`. `adapter = VllmAdapter()` 대신 후보마다 `adapter = ADAPTERS[c.engine]`; plan 직후 `platform = Platform.from_gpu(pr.gpu)`; `start(c)`는 `deps.launch(c, ADAPTERS[c.engine], platform)`. `read_applied`도 `ADAPTERS[c.engine]`로 호출.

`cli.py`의 `Deps(launch=lambda c, a: launch(c, a), ...)`를 `launch=lambda c, a, p: launch(c, a, platform=p)`로.

`report.py`: 헤더를 `| 후보 | 엔진 | max_model_len | KV 토큰 예측 / 실측 | 출력 tok/s | TTFT ms | 정확도 |`, 구분선 7칸, `cells`에 `c.engine`을 두 번째로 추가.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` → Expected: 전부 통과.

- [ ] **Step 4: 커밋**

```bash
git add -A src tests
git commit -m "Run vLLM and SGLang candidates through one engine-agnostic pipeline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 실측과 fixture 교체 (사람이 실행)

- [ ] **Step 1 (3060 Ti):** `uv run ttalkkak up --model Qwen/Qwen2.5-1.5B-Instruct --skills code-exec` → c1·c2·c3가 순서대로 뜨는지, SGLang 이미지의 entrypoint가 `python3 -m sglang.launch_server`로 동작하는지 확인. c3 컨테이너의 `docker logs`와 `curl :<port>/server_info`를 `tests/fixtures/sglang/`의 두 파일로 저장(SYNTHETIC 표시 삭제), 테스트 기대값을 실측에 맞춘다.
- [ ] **Step 2 (MI300X, AMD Developer Cloud):** `amd-smi static --asic --vram --json` 출력을 `tests/fixtures/amd/amd_smi_static.json`로 교체. `ttalkkak up`을 같은 모델로 실행하고 `seccomp=unconfined` 없이도 뜨는지 확인(필요 없으면 Platform에서 제거). ROCm에서 SGLang `fp8_e4m3`이 거부되면 c3 KV 규칙을 리포트에 적고 스펙을 갱신.
- [ ] **Step 3:** 두 장비의 `report.md`를 `runs/e2e-3060ti-stage2/`, `runs/e2e-mi300x/`에 커밋.

---

## Self-Review 결과

- 스펙 커버리지: 2절 결정(Task 6·7), 4.1 중립 인자(Task 4·5·6), 4.2 인터페이스(Task 1), 4.3 SGLang 적용값(Task 4), 4.4 Platform(Task 2), 4.5 probe(Task 3), 4.6 파서 표(Task 5), 4.7 c3 KV(Task 6), 5절 리포트·동률(Task 7), 6절 오류(Task 2 이미지 없음, Task 4 server_info 실패), 7절 테스트·실측(각 Task + Task 8).
- 1단계 대비 깨지는 인터페이스: `Launched.metrics/models_json` → `collected`, `read_applied` 인자, `Deps.launch` 인자 3개, `parsers_for` 인자 2개. 각 Task에서 기존 테스트 수정 지점을 명시했다.
- SYNTHETIC fixture: `server_info_sample.json`, `sglang/startup_sample.log`, `amd_smi_static.json` (Task 8에서 교체). SGLang 컨테이너 entrypoint 가정도 Task 8에서 확인.
