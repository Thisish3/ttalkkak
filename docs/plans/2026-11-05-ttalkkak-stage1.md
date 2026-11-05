# ttalkkak 1단계 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ttalkkak up --model <HF ID | nim:<model>> --skills a,b`가 skill 요구사항에 맞춰 vLLM 설정 후보를 만들고, Docker로 띄워 적용·정확도·에이전트 적합성·처리량을 검증해 하나를 고르고, 리포트와 함께 LangGraph 에이전트를 그 엔드포인트에 연결한다.

**Architecture:** 단계(probe → plan → launch → verify → pick → report)마다 pydantic 모델 하나를 입력받아 하나를 내고, `runs/<id>/<stage>.json`으로 저장한다. 엔진별 지식은 `engines/vllm.py`의 어댑터 안에만, 외부 프로세스(docker, guidellm, lm_eval)는 각각 작은 래퍼 함수 하나 뒤에만 둔다. 모든 외부 호출은 주입 가능한 함수/클라이언트로 받아 GPU 없이 테스트한다.

**Tech Stack:** Python 3.12, uv, typer, pydantic 2, httpx, huggingface-hub, PyYAML, langchain 1.x + langgraph + langchain-openai, langchain.mcp(+fastmcp), guidellm 0.8.1, lm-eval 0.4.13, Docker, vLLM OpenAI 이미지 v0.33.0.

**Spec:** `docs/specs/2026-11-05-ttalkkak-design.md`

## Global Constraints

- Python `>=3.12,<3.13`. 모든 명령은 `uv run ...`. 시스템 python·bare pip 금지.
- 고정 버전(2026-11-05 PyPI 확인): `pydantic==2.14.3`, `httpx==0.28.1`, `typer==0.27.3`, `huggingface-hub==2.4.0`, `langchain==1.4.9`, `langgraph==1.2.20`, `fastmcp==4.1.6`, `guidellm==0.8.1`, `lm-eval==0.4.13`. `langchain-openai`, `pyyaml`, `pytest`, `ruff`는 설치 시점 최신을 `uv.lock`에 고정.
- vLLM 이미지: `vllm/vllm-openai:v0.33.0` (Task 7 Step 1에서 태그 존재 확인. 없으면 2026-11-05 기준 최신 릴리스 태그로 바꾸고 이 줄을 수정).
- 단계 산출물 파일 이름: `probe.json`, `plan.json`, `launch.json`, `verify.json`, `pick.json`, `report.md` (스펙 4.1).
- 기동 실패 원인 분류는 정확히 `oom | bad_arg | download | timeout | unknown` 다섯 개. 로그 꼬리는 50줄.
- 원인을 판정 못하면 추측하지 않고 `unknown` + 원본 로그.
- accuracy 통과 기준 초기값: 기준 설정 대비 정답률 하락 5%p 이하.
- skill `name`: 1~64자, `[a-z0-9-]`, 하이픈으로 시작·끝 불가, `--` 불가, 폴더 이름과 일치. `description`: 1~1024자.
- 커밋 author는 `Thisish3 <104387634+Thisish3@users.noreply.github.com>`(저장소 로컬 설정 완료). 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 원격 push 금지(사용자 승인 전).

## Review Focus

1. **gated/없는 HF 모델 ID, `HF_TOKEN` 없음** → probe가 traceback 대신 "모델 접근 불가(401/404): <id>"로 종료 코드 2. (Task 3에 테스트)
2. **Docker 미설치·데몬 꺼짐** → 모델 다운로드나 후보 생성 전에 "docker 사용 불가"로 즉시 종료. (Task 7에 테스트)
3. **호스트 포트 8000 사용 중** → launch가 빈 포트를 골라 쓰고 그 포트를 `launch.json`에 기록. (Task 7에 테스트)
4. **tool_calling이 필요한데 `parsers.yaml`에 모델 계열이 없음** → plan이 파서 없이 조용히 진행하지 않고 후보에 `blockers: ["no tool parser known for <family>"]`, 전 후보가 막히면 원인 skill과 함께 실패. (Task 5에 테스트)
5. **스트리밍 tool call 인자가 여러 청크로 쪼개지고, 마지막 청크가 usage만 담거나 `[DONE]`만 옴** → 스트림 조립기가 index별로 인자를 이어 붙이고 빈 청크를 무시. (Task 8에 테스트)

---

## File Structure

```
ttalkkak/
├── pyproject.toml
├── src/ttalkkak/
│   ├── __init__.py
│   ├── schemas.py              # 모든 단계 입출력 pydantic 모델
│   ├── errors.py               # TtalkkakError(종료 코드 포함)
│   ├── cli.py                  # typer: up, plan, chat
│   ├── pipeline.py             # 단계 연결, runs 디렉터리, resume, 정리
│   ├── skills/loader.py        # SKILL.md + ttalkkak.yaml 로드·검증
│   ├── skills/requirements.py  # 요구사항 합치기(출처 기록)
│   ├── probe/hf.py             # HF config/파일 목록 → ModelInfo
│   ├── probe/gpu.py            # nvidia-smi 파싱 → GpuInfo
│   ├── plan/memory.py          # 가중치·KV 메모리 계산
│   ├── plan/parsers.py         # 모델 계열 → tool/reasoning 파서 표 조회
│   ├── plan/candidates.py      # 후보 생성 + 메모리 부족 시 대체 사다리
│   ├── data/parsers.yaml
│   ├── engines/base.py         # EngineAdapter Protocol
│   ├── engines/vllm.py         # build_args, read_applied, health
│   ├── launch/docker.py        # docker CLI 래퍼, 포트 선택, 실패 분류
│   ├── verify/openai_client.py # chat 호출 + SSE 스트림 조립
│   ├── verify/agent.py         # 에이전트 적합성 검사 4종
│   ├── verify/accuracy.py      # lm_eval 래퍼 + 결과 파싱
│   ├── verify/perf.py          # guidellm 래퍼 + 결과 파싱
│   ├── verify/applied.py       # 의도 vs 실제 비교
│   ├── pick.py
│   ├── report.py
│   ├── agent/runtime.py        # LangGraph agent + skills + MCP
│   └── mcp_servers/pyexec.py, mcp_servers/files.py
├── skills/web-fetch/  skills/code-exec/  skills/file-qa/
└── tests/ (모듈별 test_*.py, fixtures/)
```

스펙 대비 변경 한 가지: 1단계 skill `web-search`는 `web-fetch`로 이름을 바꾼다. 쓰는 MCP 서버(`mcp-server-fetch`)가 검색이 아니라 URL 가져오기만 하기 때문이다. `code-exec`과 `file-qa`의 MCP 서버는 외부 의존(Node 등)을 피하려고 fastmcp로 직접 작게 만든다(스펙 11절 5번 결정).

---

### Task 1: 프로젝트 골격과 공용 스키마

**Files:**
- Create: `pyproject.toml`, `src/ttalkkak/__init__.py`, `src/ttalkkak/schemas.py`, `src/ttalkkak/errors.py`, `src/ttalkkak/cli.py`, `tests/test_schemas.py`

**Interfaces:**
- Produces: 아래 `schemas.py`의 모든 모델 이름과 필드, `TtalkkakError(message: str, exit_code: int = 1)`, typer 앱 `ttalkkak.cli:app`.

- [ ] **Step 1: pyproject 작성**

```toml
[project]
name = "ttalkkak"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "pydantic==2.14.3", "httpx==0.28.1", "typer==0.27.3", "huggingface-hub==2.4.0",
  "pyyaml", "langchain==1.4.9", "langgraph==1.2.20", "langchain-openai", "fastmcp==4.1.6",
]
[project.optional-dependencies]
measure = ["guidellm==0.8.1", "lm-eval[api]==0.4.13"]
[dependency-groups]
dev = ["pytest", "ruff"]
[project.scripts]
ttalkkak = "ttalkkak.cli:app"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/ttalkkak"]
[tool.ruff]
line-length = 88
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run: `cd ~/Desktop/ttalkkak && uv sync --all-extras` → Expected: lock 생성, 오류 없음.

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_schemas.py
from ttalkkak.schemas import Candidate, Requirements, RunState


def test_candidate_roundtrip_keeps_blockers():
    c = Candidate(id="c1", engine="vllm", args={"max_model_len": 8192},
                  predicted={"kv_gib": 1.2}, reasons=["fits"], blockers=["x"])
    assert Candidate.model_validate_json(c.model_dump_json()) == c


def test_requirements_default_is_empty():
    r = Requirements()
    assert r.flags == {} and r.min_context == 0 and r.sources == {}


def test_runstate_tracks_done_stages():
    s = RunState(run_id="r1", done=["probe"])
    assert "probe" in s.done
```

Run: `uv run pytest tests/test_schemas.py -v` → Expected: FAIL (`ModuleNotFoundError: ttalkkak.schemas`).

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/errors.py
class TtalkkakError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code
```

```python
# src/ttalkkak/schemas.py
from typing import Literal

from pydantic import BaseModel, Field

Flag = Literal["tool_calling", "reasoning", "streaming", "structured_output",
               "single_tool_call"]
FailureKind = Literal["oom", "bad_arg", "download", "timeout", "unknown"]


class GpuInfo(BaseModel):
    name: str
    memory_mib: int
    compute_capability: str


class ModelInfo(BaseModel):
    model_id: str
    family: str                 # config.json model_type, 예: "qwen2"
    num_layers: int
    num_kv_heads: int
    head_dim: int
    dtype: str                  # "bfloat16" 등
    quantization: str | None    # quantization_config.quant_method
    max_position: int
    weight_bytes: int           # *.safetensors 합


class ProbeResult(BaseModel):
    model: ModelInfo | None     # nim: 모델이면 None
    gpu: GpuInfo | None
    remote: str | None = None   # "nim:<model>"


class Requirements(BaseModel):
    flags: dict[Flag, bool] = Field(default_factory=dict)
    min_context: int = 0
    sources: dict[str, list[str]] = Field(default_factory=dict)  # 항목 → skill 이름들


class Candidate(BaseModel):
    id: str
    engine: Literal["vllm"]
    model_id: str
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)
    predicted: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    requires_accuracy: bool = False   # fp8 KV 후보 등


class Plan(BaseModel):
    candidates: list[Candidate]
    requirements: Requirements


class Launched(BaseModel):
    candidate_id: str
    status: Literal["ready", "failed"]
    base_url: str | None = None
    container: str | None = None
    failure: FailureKind | None = None
    log_tail: list[str] = Field(default_factory=list)
    logs: str = ""
    metrics: str = ""
    models_json: dict = Field(default_factory=dict)


class AppliedValue(BaseModel):
    value: str | int | float | bool | None
    evidence: str               # 로그 줄 또는 metric/endpoint 이름, 못 읽으면 "unknown"


class AppliedConfig(BaseModel):
    values: dict[str, AppliedValue] = Field(default_factory=dict)


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class CandidateVerdict(BaseModel):
    candidate_id: str
    applied: AppliedConfig = Field(default_factory=AppliedConfig)
    mismatches: list[str] = Field(default_factory=list)
    accuracy: float | None = None
    accuracy_passed: bool | None = None
    agent: list[CheckResult] = Field(default_factory=list)
    perf: dict[str, float] = Field(default_factory=dict)


class Verification(BaseModel):
    verdicts: list[CandidateVerdict]
    baseline_accuracy: float | None = None


class Pick(BaseModel):
    chosen: str | None
    rejected: dict[str, str] = Field(default_factory=dict)   # id → 이유
    base_url: str | None = None


class RunState(BaseModel):
    run_id: str
    done: list[str] = Field(default_factory=list)
```

```python
# src/ttalkkak/cli.py
import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """ttalkkak: agent-ready LLM serving."""
```

`src/ttalkkak/__init__.py`는 빈 파일.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_schemas.py -v && uv run ruff check .` → Expected: 3 passed, ruff 경고 없음.

- [ ] **Step 5: 커밋**

```bash
git add pyproject.toml uv.lock src tests/test_schemas.py
git commit -m "Add project skeleton and stage schemas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: skill 로더와 요구사항 합치기

**Files:**
- Create: `src/ttalkkak/skills/__init__.py`, `src/ttalkkak/skills/loader.py`, `src/ttalkkak/skills/requirements.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: `Requirements`, `TtalkkakError` (Task 1)
- Produces:
  - `Skill(name: str, description: str, body: str, path: Path, ext: SkillExt)`
  - `SkillExt(requires: dict[Flag, bool], min_context: int, mcp_servers: list[McpServer])`, `McpServer(name: str, command: list[str])`
  - `load_skill(path: Path) -> Skill`, `load_skills(root: Path, names: list[str]) -> list[Skill]`
  - `merge_requirements(skills: list[Skill]) -> Requirements`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_skills.py
from pathlib import Path

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.skills.loader import load_skill
from ttalkkak.skills.requirements import merge_requirements


def make(tmp: Path, name: str, front: str, ext: str | None = None) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{front}\n---\n본문 지침\n")
    if ext is not None:
        (d / "ttalkkak.yaml").write_text(ext)
    return d


def test_loads_standard_skill_without_ext(tmp_path):
    s = load_skill(make(tmp_path, "a-b", "name: a-b\ndescription: does x"))
    assert s.name == "a-b" and s.body.strip() == "본문 지침"
    assert s.ext.requires == {} and s.ext.min_context == 0


@pytest.mark.parametrize("bad", ["A-b", "-ab", "ab-", "a--b", "x" * 65])
def test_rejects_bad_names(tmp_path, bad):
    d = make(tmp_path, "dir", f"name: {bad}\ndescription: d")
    with pytest.raises(TtalkkakError):
        load_skill(d)


def test_name_must_match_directory(tmp_path):
    with pytest.raises(TtalkkakError, match="directory"):
        load_skill(make(tmp_path, "other", "name: a-b\ndescription: d"))


def test_rejects_unknown_requirement_flag(tmp_path):
    d = make(tmp_path, "a", "name: a\ndescription: d", "requires:\n  telepathy: true\n")
    with pytest.raises(TtalkkakError):
        load_skill(d)


def test_merge_takes_union_and_max_with_sources(tmp_path):
    a = load_skill(make(tmp_path, "a", "name: a\ndescription: d",
                        "requires:\n  tool_calling: true\nmin_context: 16384\n"))
    b = load_skill(make(tmp_path, "b", "name: b\ndescription: d",
                        "requires:\n  tool_calling: true\n  reasoning: true\n"
                        "min_context: 32768\n"))
    r = merge_requirements([a, b])
    assert r.flags == {"tool_calling": True, "reasoning": True}
    assert r.min_context == 32768
    assert r.sources["tool_calling"] == ["a", "b"]
    assert r.sources["min_context"] == ["b"]
```

Run: `uv run pytest tests/test_skills.py -v` → Expected: FAIL (모듈 없음).

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/skills/loader.py
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import Flag

_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    command: list[str]


class SkillExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requires: dict[Flag, bool] = Field(default_factory=dict)
    min_context: int = 0
    mcp_servers: list[McpServer] = Field(default_factory=list)


class Skill(BaseModel):
    name: str
    description: str
    body: str
    path: Path
    ext: SkillExt


def _split_front(text: str, path: Path) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise TtalkkakError(f"{path}: SKILL.md must start with YAML frontmatter")
    _, front, body = text.split("---\n", 2)
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise TtalkkakError(f"{path}: frontmatter must be a mapping")
    return data, body


def load_skill(path: Path) -> Skill:
    md = path / "SKILL.md"
    if not md.is_file():
        raise TtalkkakError(f"{path}: SKILL.md not found")
    front, body = _split_front(md.read_text(encoding="utf-8"), md)
    name, desc = str(front.get("name", "")), str(front.get("description", ""))
    if not (1 <= len(name) <= 64 and _NAME.match(name)):
        raise TtalkkakError(f"{md}: invalid skill name {name!r}")
    if name != path.name:
        raise TtalkkakError(f"{md}: name {name!r} must match directory {path.name!r}")
    if not 1 <= len(desc) <= 1024:
        raise TtalkkakError(f"{md}: description must be 1-1024 characters")
    ext_file = path / "ttalkkak.yaml"
    raw = yaml.safe_load(ext_file.read_text(encoding="utf-8")) if ext_file.is_file() else {}
    try:
        ext = SkillExt.model_validate(raw or {})
    except ValidationError as err:
        raise TtalkkakError(f"{ext_file}: {err}") from err
    return Skill(name=name, description=desc, body=body, path=path, ext=ext)


def load_skills(root: Path, names: list[str]) -> list[Skill]:
    return [load_skill(root / n) for n in names]
```

```python
# src/ttalkkak/skills/requirements.py
from ttalkkak.schemas import Requirements
from ttalkkak.skills.loader import Skill


def merge_requirements(skills: list[Skill]) -> Requirements:
    req = Requirements()
    for s in skills:
        for flag, on in s.ext.requires.items():
            if on:
                req.flags[flag] = True
                req.sources.setdefault(flag, []).append(s.name)
        if s.ext.min_context > req.min_context:
            req.min_context = s.ext.min_context
            req.sources["min_context"] = [s.name]
        elif s.ext.min_context and s.ext.min_context == req.min_context:
            req.sources.setdefault("min_context", []).append(s.name)
    return req
```

`src/ttalkkak/skills/__init__.py`는 빈 파일.

- [ ] **Step 3: 통과 확인**

Run: `uv run pytest tests/test_skills.py -v` → Expected: 9 passed.

- [ ] **Step 4: 커밋**

```bash
git add src/ttalkkak/skills tests/test_skills.py
git commit -m "Add skill loader and requirement merging

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: probe (HF 모델 정보, GPU 정보)

**Files:**
- Create: `src/ttalkkak/probe/__init__.py`, `src/ttalkkak/probe/hf.py`, `src/ttalkkak/probe/gpu.py`, `tests/test_probe.py`, `tests/fixtures/hf/qwen2.5-7b-instruct-awq.json`

**Interfaces:**
- Consumes: `ModelInfo`, `GpuInfo`, `ProbeResult`, `TtalkkakError`
- Produces:
  - `model_info_from(model_id: str, config: dict, file_sizes: dict[str, int]) -> ModelInfo`
  - `fetch_model_info(model_id: str, api: HfApi | None = None) -> ModelInfo` (오류 시 `TtalkkakError(exit_code=2)`)
  - `parse_nvidia_smi(csv: str) -> GpuInfo`, `detect_gpu(run=subprocess.run) -> GpuInfo | None`
  - `probe(model: str, api=None, run=subprocess.run) -> ProbeResult` (`model`이 `nim:`으로 시작하면 HF·GPU 조회 없이 `remote`만 채움)

- [ ] **Step 1: fixture 저장**

Run (HF에서 실제 config 받기, 네트워크 필요):
```bash
uv run python -c "import json,huggingface_hub as h;p=h.hf_hub_download('Qwen/Qwen2.5-7B-Instruct-AWQ','config.json');print(open(p).read())" > tests/fixtures/hf/qwen2.5-7b-instruct-awq.json
```
Expected: `model_type`가 `qwen2`, `num_hidden_layers` 28, `num_key_value_heads` 4, `quantization_config.quant_method`가 `awq`. 값이 다르면 아래 테스트의 기대값을 파일 값으로 바꾼다.

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_probe.py
import json
from pathlib import Path

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.probe import probe
from ttalkkak.probe.gpu import parse_nvidia_smi
from ttalkkak.probe.hf import fetch_model_info, model_info_from

FIX = Path(__file__).parent / "fixtures"


def test_model_info_from_qwen_awq():
    cfg = json.loads((FIX / "hf/qwen2.5-7b-instruct-awq.json").read_text())
    info = model_info_from("Qwen/Qwen2.5-7B-Instruct-AWQ", cfg,
                           {"model-00001.safetensors": 3_000, "model-00002.safetensors": 2_000,
                            "tokenizer.json": 7})
    assert info.family == "qwen2" and info.num_layers == 28 and info.num_kv_heads == 4
    assert info.head_dim == cfg["hidden_size"] // cfg["num_attention_heads"]
    assert info.quantization == "awq" and info.weight_bytes == 5_000


def test_parse_nvidia_smi():
    g = parse_nvidia_smi("NVIDIA GeForce RTX 3060 Ti, 8192, 8.6\n")
    assert g.name == "NVIDIA GeForce RTX 3060 Ti" and g.memory_mib == 8192
    assert g.compute_capability == "8.6"


class _DeniedApi:
    def model_info(self, *a, **k):
        from huggingface_hub.errors import GatedRepoError
        raise GatedRepoError("gated", response=None)


def test_gated_model_is_a_clear_error():           # Review Focus 1
    with pytest.raises(TtalkkakError) as e:
        fetch_model_info("meta-llama/Llama-3.1-8B-Instruct", api=_DeniedApi())
    assert e.value.exit_code == 2 and "접근 불가" in str(e.value)


def test_nim_model_skips_hf_and_gpu():
    r = probe("nim:meta/llama-3.1-8b-instruct", api=object(), run=None)
    assert r.remote == "nim:meta/llama-3.1-8b-instruct" and r.model is None
```

Run: `uv run pytest tests/test_probe.py -v` → Expected: FAIL (모듈 없음).

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/probe/hf.py
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
import json

from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import ModelInfo


def model_info_from(model_id: str, config: dict, file_sizes: dict[str, int]) -> ModelInfo:
    heads = config["num_attention_heads"]
    quant = (config.get("quantization_config") or {}).get("quant_method")
    return ModelInfo(
        model_id=model_id,
        family=config["model_type"],
        num_layers=config["num_hidden_layers"],
        num_kv_heads=config.get("num_key_value_heads", heads),
        head_dim=config.get("head_dim") or config["hidden_size"] // heads,
        dtype=str(config.get("torch_dtype") or config.get("dtype") or "bfloat16"),
        quantization=quant,
        max_position=config.get("max_position_embeddings", 0),
        weight_bytes=sum(v for k, v in file_sizes.items() if k.endswith(".safetensors")),
    )


def fetch_model_info(model_id: str, api: HfApi | None = None) -> ModelInfo:
    api = api or HfApi()
    try:
        info = api.model_info(model_id, files_metadata=True)
        path = hf_hub_download(model_id, "config.json")
    except (GatedRepoError, RepositoryNotFoundError) as err:
        raise TtalkkakError(
            f"모델 접근 불가(401/404): {model_id}. HF_TOKEN과 모델 접근 권한을 확인하세요.",
            exit_code=2) from err
    sizes = {s.rfilename: s.size or 0 for s in info.siblings or []}
    with open(path, encoding="utf-8") as f:
        return model_info_from(model_id, json.load(f), sizes)
```

```python
# src/ttalkkak/probe/gpu.py
import subprocess

from ttalkkak.schemas import GpuInfo

QUERY = ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap",
         "--format=csv,noheader,nounits"]


def parse_nvidia_smi(csv: str) -> GpuInfo:
    name, mem, cc = [p.strip() for p in csv.strip().splitlines()[0].split(",")]
    return GpuInfo(name=name, memory_mib=int(mem), compute_capability=cc)


def detect_gpu(run=subprocess.run) -> GpuInfo | None:
    try:
        out = run(QUERY, capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return parse_nvidia_smi(out.stdout)
```

```python
# src/ttalkkak/probe/__init__.py
import subprocess

from ttalkkak.probe.gpu import detect_gpu
from ttalkkak.probe.hf import fetch_model_info
from ttalkkak.schemas import ProbeResult


def probe(model: str, api=None, run=subprocess.run) -> ProbeResult:
    if model.startswith("nim:"):
        return ProbeResult(model=None, gpu=None, remote=model)
    return ProbeResult(model=fetch_model_info(model, api=api), gpu=detect_gpu(run))
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_probe.py -v` → Expected: 4 passed. `GatedRepoError(…, response=None)` 생성자가 huggingface-hub 2.4.0에서 다르면, 테스트의 `_DeniedApi`가 `GatedRepoError.__new__(GatedRepoError)`로 만들도록 바꾼다(코드 쪽은 그대로).

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/probe tests/test_probe.py tests/fixtures/hf
git commit -m "Add model and GPU probing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 메모리 계산

**Files:**
- Create: `src/ttalkkak/plan/__init__.py`, `src/ttalkkak/plan/memory.py`, `tests/test_memory.py`

**Interfaces:**
- Consumes: `ModelInfo`, `GpuInfo`
- Produces:
  - `kv_bytes_per_token(m: ModelInfo, kv_dtype: str) -> int` (`kv_dtype` ∈ `"auto"`, `"fp8"`; auto는 모델 dtype 2바이트, fp8은 1바이트)
  - `usable_bytes(g: GpuInfo, utilization: float = 0.9, overhead_gib: float = 1.0) -> int`
  - `max_context_fit(m: ModelInfo, g: GpuInfo, kv_dtype: str) -> int` (가중치를 뺀 뒤 KV에 들어가는 토큰 수, 음수면 0)

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_memory.py
from ttalkkak.plan.memory import kv_bytes_per_token, max_context_fit, usable_bytes
from ttalkkak.schemas import GpuInfo, ModelInfo

QWEN7 = ModelInfo(model_id="q", family="qwen2", num_layers=28, num_kv_heads=4, head_dim=128,
                  dtype="float16", quantization="awq", max_position=32768,
                  weight_bytes=5_580_000_000)
GPU8 = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def test_kv_bytes_formula():
    # 2(K,V) × 28 × 4 × 128 × 2 bytes
    assert kv_bytes_per_token(QWEN7, "auto") == 57_344
    assert kv_bytes_per_token(QWEN7, "fp8") == 28_672


def test_usable_bytes():
    assert usable_bytes(GPU8) == int(8192 * 2**20 * 0.9) - 2**30


def test_fp8_roughly_doubles_context():
    a, b = max_context_fit(QWEN7, GPU8, "auto"), max_context_fit(QWEN7, GPU8, "fp8")
    assert a > 0 and abs(b - 2 * a) <= 1


def test_does_not_go_negative():
    big = QWEN7.model_copy(update={"weight_bytes": 20 * 2**30})
    assert max_context_fit(big, GPU8, "auto") == 0
```

Run: `uv run pytest tests/test_memory.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/plan/memory.py
from ttalkkak.schemas import GpuInfo, ModelInfo

_BYTES = {"fp8": 1, "auto": 2}


def kv_bytes_per_token(m: ModelInfo, kv_dtype: str) -> int:
    return 2 * m.num_layers * m.num_kv_heads * m.head_dim * _BYTES[kv_dtype]


def usable_bytes(g: GpuInfo, utilization: float = 0.9, overhead_gib: float = 1.0) -> int:
    return int(g.memory_mib * 2**20 * utilization) - int(overhead_gib * 2**30)


def max_context_fit(m: ModelInfo, g: GpuInfo, kv_dtype: str) -> int:
    free = usable_bytes(g) - m.weight_bytes
    return max(0, free // kv_bytes_per_token(m, kv_dtype))
```

`overhead_gib=1.0`(activation·CUDA graph 여유)은 예측용 가정이다. 실제 KV 용량은 Task 6의 `read_applied`가 로그에서 읽어 리포트에서 대조한다.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/test_memory.py -v` → Expected: 4 passed.

- [ ] **Step 4: 커밋**

```bash
git add src/ttalkkak/plan tests/test_memory.py
git commit -m "Add weight and KV memory estimates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 파서 표와 후보 생성

**Files:**
- Create: `src/ttalkkak/data/parsers.yaml`, `src/ttalkkak/plan/parsers.py`, `src/ttalkkak/plan/candidates.py`, `tests/test_candidates.py`

**Interfaces:**
- Consumes: `kv_bytes_per_token`, `max_context_fit` (Task 4), `ModelInfo`, `GpuInfo`, `Requirements`, `Candidate`, `Plan`
- Produces:
  - `parsers_for(family: str) -> ParserEntry | None`, `ParserEntry(tool: str | None, reasoning: str | None)`
  - `make_plan(probe: ProbeResult, req: Requirements) -> Plan`

후보 규칙(스펙 6.4):
1. `c1`: KV `auto`, `max_model_len = min(model.max_position, max_context_fit(auto))`. `req.min_context`보다 작으면 blocker.
2. `c2`: KV `fp8`, 같은 방식, `requires_accuracy=True`. c1이 blocker 없이 통과하고 fp8 없이도 `min_context`를 만족해도, 비교용으로 항상 만든다(처리량·정확도 비교가 리포트의 재료).
3. 둘 다 blocker이면 `reasons`에 "양자화본 또는 더 작은 모델 필요"와 원인 skill(`req.sources["min_context"]`)을 적는다. 양자화본 자동 검색은 1단계 범위 밖이다(스펙 6.4의 2, 3번은 리포트 안내로만 남긴다).
- tool_calling이 필요하면 `enable_auto_tool_choice=True`, `tool_call_parser=<표>`. 표에 없으면 모든 후보에 blocker(Review Focus 4).
- reasoning이 필요하면 `reasoning_parser=<표>`. 없으면 blocker.
- 공통 args: `gpu_memory_utilization=0.9`.

- [ ] **Step 1: 파서 표 작성**

```yaml
# src/ttalkkak/data/parsers.yaml
# model_type → vLLM parser names (vLLM 0.33 docs 기준, verify.agent로 실측 확인)
qwen2:  {tool: hermes, reasoning: null}
qwen3:  {tool: hermes, reasoning: qwen3}
llama:  {tool: llama3_json, reasoning: null}
mistral: {tool: mistral, reasoning: null}
```

Run: `docker run --rm vllm/vllm-openai:v0.33.0 --help=Frontend 2>/dev/null | grep -A3 tool-call-parser` (데스크탑에서, Task 7 이후 가능). Expected: `hermes`, `llama3_json`, `mistral`이 선택지에 있음. 없는 이름은 표에서 고친다.

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_candidates.py
from ttalkkak.plan.candidates import make_plan
from ttalkkak.schemas import GpuInfo, ModelInfo, ProbeResult, Requirements

M = ModelInfo(model_id="Qwen/Qwen2.5-7B-Instruct-AWQ", family="qwen2", num_layers=28,
              num_kv_heads=4, head_dim=128, dtype="float16", quantization="awq",
              max_position=32768, weight_bytes=5_580_000_000)
G = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def req(**kw):
    return Requirements(**kw)


def test_two_candidates_auto_and_fp8():
    p = make_plan(ProbeResult(model=M, gpu=G), req())
    assert [c.args["kv_cache_dtype"] for c in p.candidates] == ["auto", "fp8"]
    assert p.candidates[1].requires_accuracy


def test_tool_calling_sets_parser():
    p = make_plan(ProbeResult(model=M, gpu=G),
                  req(flags={"tool_calling": True}, sources={"tool_calling": ["web-fetch"]}))
    c = p.candidates[0]
    assert c.args["enable_auto_tool_choice"] is True and c.args["tool_call_parser"] == "hermes"


def test_unknown_family_blocks_when_tools_needed():           # Review Focus 4
    m = M.model_copy(update={"family": "unknownlm"})
    p = make_plan(ProbeResult(model=m, gpu=G), req(flags={"tool_calling": True}))
    assert all("no tool parser known for unknownlm" in c.blockers for c in p.candidates)


def test_min_context_too_big_blocks_and_names_skill():
    p = make_plan(ProbeResult(model=M, gpu=G),
                  req(min_context=10**6, sources={"min_context": ["file-qa"]}))
    assert all(c.blockers for c in p.candidates)
    assert any("file-qa" in r for r in p.candidates[0].reasons)


def test_max_model_len_never_exceeds_model_limit():
    big_gpu = G.model_copy(update={"memory_mib": 192 * 1024})
    p = make_plan(ProbeResult(model=M, gpu=big_gpu), req())
    assert all(c.args["max_model_len"] == 32768 for c in p.candidates)
```

Run: `uv run pytest tests/test_candidates.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/plan/parsers.py
from functools import cache
from importlib.resources import files

import yaml
from pydantic import BaseModel


class ParserEntry(BaseModel):
    tool: str | None
    reasoning: str | None


@cache
def _table() -> dict[str, ParserEntry]:
    raw = yaml.safe_load(files("ttalkkak.data").joinpath("parsers.yaml").read_text())
    return {k: ParserEntry.model_validate(v) for k, v in raw.items()}


def parsers_for(family: str) -> ParserEntry | None:
    return _table().get(family)
```

`src/ttalkkak/data/__init__.py`(빈 파일)도 만든다(`importlib.resources`용).

```python
# src/ttalkkak/plan/candidates.py
from ttalkkak.errors import TtalkkakError
from ttalkkak.plan.memory import kv_bytes_per_token, max_context_fit
from ttalkkak.plan.parsers import parsers_for
from ttalkkak.schemas import Candidate, Plan, ProbeResult, Requirements


def _parser_args(family: str, req: Requirements) -> tuple[dict, list[str]]:
    args: dict = {}
    blockers: list[str] = []
    entry = parsers_for(family)
    if req.flags.get("tool_calling"):
        if entry and entry.tool:
            args |= {"enable_auto_tool_choice": True, "tool_call_parser": entry.tool}
        else:
            blockers.append(f"no tool parser known for {family}")
    if req.flags.get("reasoning"):
        if entry and entry.reasoning:
            args["reasoning_parser"] = entry.reasoning
        else:
            blockers.append(f"no reasoning parser known for {family}")
    return args, blockers


def make_plan(probe: ProbeResult, req: Requirements) -> Plan:
    m, g = probe.model, probe.gpu
    if m is None or g is None:
        raise TtalkkakError("로컬 서빙 계획에는 HF 모델 정보와 NVIDIA GPU가 필요합니다", 2)
    parser_args, parser_blockers = _parser_args(m.family, req)
    out = []
    for i, kv in enumerate(["auto", "fp8"], start=1):
        fit = max_context_fit(m, g, kv)
        length = min(m.max_position, fit)
        blockers = list(parser_blockers)
        reasons = [f"KV {kv}: {kv_bytes_per_token(m, kv)} B/token, 들어가는 문맥 약 {fit} 토큰"]
        if length < max(req.min_context, 1):
            blockers.append(f"max_model_len {length} < 필요 {req.min_context}")
            who = ", ".join(req.sources.get("min_context", [])) or "요청"
            reasons.append(f"{who}의 min_context {req.min_context} 때문에 불가. "
                           "양자화본 또는 더 작은 모델 필요")
        out.append(Candidate(
            id=f"c{i}", engine="vllm", model_id=m.model_id,
            args={"kv_cache_dtype": kv, "max_model_len": length,
                  "gpu_memory_utilization": 0.9} | parser_args,
            predicted={"weight_gib": m.weight_bytes / 2**30,
                       "kv_tokens_fit": float(fit)},
            reasons=reasons, blockers=blockers, requires_accuracy=(kv == "fp8"),
        ))
    return Plan(candidates=out, requirements=req)
```

`max_model_len`이 0인 후보(가중치조차 안 들어감)도 blocker로 남는다(`length < 1`).

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_candidates.py -v` → Expected: 5 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/data src/ttalkkak/plan tests/test_candidates.py
git commit -m "Generate serving candidates from requirements

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: vLLM 어댑터 (인자 생성, 적용 값 읽기)

**Files:**
- Create: `src/ttalkkak/engines/__init__.py`, `src/ttalkkak/engines/base.py`, `src/ttalkkak/engines/vllm.py`, `tests/test_vllm_adapter.py`, `tests/fixtures/vllm/startup_sample.log`, `tests/fixtures/vllm/metrics_sample.txt`

**Interfaces:**
- Consumes: `Candidate`, `AppliedConfig`, `AppliedValue`
- Produces:
  - `ContainerSpec(image: str, args: list[str], env: dict[str, str], container_port: int = 8000)`
  - `EngineAdapter` Protocol: `name`, `build_args(c: Candidate) -> ContainerSpec`, `read_applied(logs: str, metrics: str, models: dict) -> AppliedConfig`, `health(base_url: str, client: httpx.Client) -> bool`
  - `VllmAdapter` (`IMAGE = "vllm/vllm-openai:v0.33.0"`)
  - 적용 값 키: `max_model_len`, `kv_cache_dtype`, `kv_cache_tokens`, `attention_backend`, `tool_call_parser`, `reasoning_parser`, `enable_auto_tool_choice`

- [ ] **Step 1: fixture 작성 (임시, Task 15에서 실측 로그로 교체)**

`tests/fixtures/vllm/startup_sample.log` (vLLM 소스의 로그 형식 문자열 기준으로 만든 표본. 실제 로그가 아님을 파일 첫 줄에 적는다):
```
# SYNTHETIC: built from vLLM log format strings; replace with a real capture (Task 15)
INFO 11-05 12:00:00 [api_utils.py:273] non-default args: {'model': 'Qwen/Qwen2.5-7B-Instruct-AWQ', 'max_model_len': 16384, 'kv_cache_dtype': 'fp8', 'enable_auto_tool_choice': True, 'tool_call_parser': 'hermes'}
INFO 11-05 12:00:05 [model.py:1926] Using max model len 16384
INFO 11-05 12:00:20 [cuda.py:422] Using FLASH_ATTN backend.
INFO 11-05 12:00:40 [kv_cache_utils.py:1862] GPU KV cache size: 71,632 tokens, Maximum concurrency for 16384 tokens per request: 4.37x
```

`tests/fixtures/vllm/metrics_sample.txt`:
```
# SYNTHETIC: replace with a real /metrics capture (Task 15)
vllm:cache_config_info{block_size="16",cache_dtype="fp8",engine="0",gpu_memory_utilization="0.9",num_gpu_blocks="4477"} 1.0
```

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_vllm_adapter.py
from pathlib import Path

import httpx

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.schemas import Candidate

FIX = Path(__file__).parent / "fixtures/vllm"
A = VllmAdapter()


def test_build_args_maps_flags():
    c = Candidate(id="c1", engine="vllm", model_id="m/x",
                  args={"max_model_len": 16384, "kv_cache_dtype": "fp8",
                        "enable_auto_tool_choice": True, "tool_call_parser": "hermes",
                        "gpu_memory_utilization": 0.9})
    spec = A.build_args(c)
    assert spec.image == "vllm/vllm-openai:v0.33.0"
    assert spec.args[:2] == ["--model", "m/x"]
    assert "--enable-auto-tool-choice" in spec.args
    i = spec.args.index("--tool-call-parser")
    assert spec.args[i + 1] == "hermes"
    assert "--max-model-len" in spec.args and "16384" in spec.args


def test_false_bool_flag_is_omitted():
    c = Candidate(id="c", engine="vllm", model_id="m", args={"enable_auto_tool_choice": False})
    assert "--enable-auto-tool-choice" not in A.build_args(c).args


def test_read_applied_from_logs_metrics_and_models():
    a = A.read_applied((FIX / "startup_sample.log").read_text(),
                       (FIX / "metrics_sample.txt").read_text(),
                       {"data": [{"id": "m", "max_model_len": 16384}]})
    v = a.values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_dtype"].value == "fp8"
    assert v["kv_cache_tokens"].value == 71632
    assert v["attention_backend"].value == "FLASH_ATTN"
    assert v["tool_call_parser"].value == "hermes"
    assert "cache_config_info" in v["kv_cache_dtype"].evidence


def test_missing_values_are_unknown():
    a = A.read_applied("", "", {})
    assert a.values["attention_backend"].value is None
    assert a.values["attention_backend"].evidence == "unknown"


def test_health():
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert A.health("http://x", ok) and not A.health("http://x", bad)
```

Run: `uv run pytest tests/test_vllm_adapter.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/engines/base.py
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from ttalkkak.schemas import AppliedConfig, Candidate


class ContainerSpec(BaseModel):
    image: str
    args: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    container_port: int = 8000


class EngineAdapter(Protocol):
    name: str

    def build_args(self, c: Candidate) -> ContainerSpec: ...
    def read_applied(self, logs: str, metrics: str, models: dict) -> AppliedConfig: ...
    def health(self, base_url: str, client: httpx.Client) -> bool: ...
```

```python
# src/ttalkkak/engines/vllm.py
import ast
import re

import httpx

from ttalkkak.engines.base import ContainerSpec
from ttalkkak.schemas import AppliedConfig, AppliedValue, Candidate

_NON_DEFAULT = re.compile(r"non-default args: (\{.*\})")
_MAX_LEN = re.compile(r"Using max model len (\d+)")
_KV_TOKENS = re.compile(r"GPU KV cache size: ([\d,]+) tokens")
_BACKEND = re.compile(r"Using (\S+) backend\.")
_CACHE_INFO = re.compile(r"vllm:cache_config_info\{([^}]*)\}")


def _labels(s: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', s))


def _find(pattern: re.Pattern, text: str) -> tuple[str, str] | None:
    for line in text.splitlines():
        if m := pattern.search(line):
            return m.group(1), line.strip()
    return None


class VllmAdapter:
    name = "vllm"
    IMAGE = "vllm/vllm-openai:v0.33.0"

    def build_args(self, c: Candidate) -> ContainerSpec:
        args = ["--model", c.model_id, "--host", "0.0.0.0", "--port", "8000"]
        for key, val in c.args.items():
            flag = "--" + key.replace("_", "-")
            if val is True:
                args.append(flag)
            elif val is not False:
                args += [flag, str(val)]
        return ContainerSpec(image=self.IMAGE, args=args)

    def read_applied(self, logs: str, metrics: str, models: dict) -> AppliedConfig:
        v: dict[str, AppliedValue] = {}

        def put(key, value, evidence):
            v[key] = AppliedValue(value=value, evidence=evidence)

        for key in ("max_model_len", "kv_cache_dtype", "kv_cache_tokens",
                    "attention_backend", "tool_call_parser", "reasoning_parser",
                    "enable_auto_tool_choice"):
            put(key, None, "unknown")

        if hit := _find(_NON_DEFAULT, logs):
            args = ast.literal_eval(hit[0])
            for key in ("tool_call_parser", "reasoning_parser", "enable_auto_tool_choice",
                        "kv_cache_dtype"):
                if key in args:
                    put(key, args[key], hit[1])
        if hit := _find(_MAX_LEN, logs):
            put("max_model_len", int(hit[0]), hit[1])
        data = models.get("data") or []
        if data and data[0].get("max_model_len"):
            put("max_model_len", int(data[0]["max_model_len"]), "/v1/models max_model_len")
        if hit := _find(_KV_TOKENS, logs):
            put("kv_cache_tokens", int(hit[0].replace(",", "")), hit[1])
        if hit := _find(_BACKEND, logs):
            put("attention_backend", hit[0], hit[1])
        if m := _CACHE_INFO.search(metrics):
            lab = _labels(m.group(1))
            if "cache_dtype" in lab:
                put("kv_cache_dtype", lab["cache_dtype"], "metric vllm:cache_config_info")
        return AppliedConfig(values=v)

    def health(self, base_url: str, client: httpx.Client) -> bool:
        try:
            return client.get(f"{base_url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False
```

`src/ttalkkak/engines/__init__.py`는 빈 파일.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_vllm_adapter.py -v` → Expected: 5 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/engines tests/test_vllm_adapter.py tests/fixtures/vllm
git commit -m "Add vLLM adapter for args and applied config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: launch (Docker)

**Files:**
- Create: `src/ttalkkak/launch/__init__.py`, `src/ttalkkak/launch/docker.py`, `tests/test_launch.py`

**Interfaces:**
- Consumes: `ContainerSpec`, `EngineAdapter`, `Candidate`, `Launched`, `TtalkkakError`
- Produces:
  - `check_docker(run=subprocess.run) -> None` (불가 시 `TtalkkakError(exit_code=3)`)
  - `free_port(preferred: int = 8000) -> int`
  - `classify_failure(logs: str, timed_out: bool) -> FailureKind`
  - `launch(c: Candidate, adapter: EngineAdapter, run=subprocess.run, client: httpx.Client | None = None, wait_s: int = 900, sleep=time.sleep) -> Launched`
  - `stop(container: str, run=subprocess.run) -> None`

- [ ] **Step 1: 이미지 태그 확인**

Run: `docker manifest inspect vllm/vllm-openai:v0.33.0 > /dev/null && echo ok` → Expected: `ok`. 아니면 Global Constraints와 `VllmAdapter.IMAGE`를 실제 최신 태그로 바꾼다.

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_launch.py
import socket
import subprocess

import httpx
import pytest

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.launch.docker import check_docker, classify_failure, free_port, launch
from ttalkkak.schemas import Candidate

C = Candidate(id="c1", engine="vllm", model_id="m/x", args={"max_model_len": 4096})


def test_docker_missing_is_clear_error():                    # Review Focus 2
    def run(*a, **k):
        raise FileNotFoundError("docker")
    with pytest.raises(TtalkkakError) as e:
        check_docker(run)
    assert e.value.exit_code == 3 and "docker" in str(e.value)


def test_free_port_skips_busy_port():                        # Review Focus 3
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    busy = s.getsockname()[1]
    try:
        assert free_port(busy) != busy
    finally:
        s.close()


@pytest.mark.parametrize("log,kind", [
    ("torch.OutOfMemoryError: CUDA out of memory", "oom"),
    ("ValueError: No available memory for the cache blocks", "oom"),
    ("api_server.py: error: unrecognized arguments: --foo", "bad_arg"),
    ("huggingface_hub.errors.HfHubHTTPError: 401", "download"),
    ("something odd", "unknown"),
])
def test_classify_failure(log, kind):
    assert classify_failure(log, timed_out=False) == kind


def test_classify_timeout_wins_when_no_signal():
    assert classify_failure("still loading", timed_out=True) == "timeout"


class FakeDocker:
    def __init__(self, exit_early=False):
        self.calls, self.exit_early = [], exit_early

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[:2] == ["docker", "run"]:
            out = "cid123\n"
        elif cmd[:2] == ["docker", "logs"]:
            out = "CUDA out of memory" if self.exit_early else "Using max model len 4096"
        elif cmd[:2] == ["docker", "inspect"]:
            out = "exited" if self.exit_early else "running"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _client(status):
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m/x", "max_model_len": 4096}]})
        if req.url.path == "/metrics":
            return httpx.Response(200, text="")
        return httpx.Response(status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_launch_ready_collects_logs_and_models():
    fake = FakeDocker()
    r = launch(C, VllmAdapter(), run=fake, client=_client(200), wait_s=5, sleep=lambda s: None)
    assert r.status == "ready" and r.container == "cid123"
    assert r.models_json["data"][0]["max_model_len"] == 4096
    run_cmd = fake.calls[0]
    assert "--gpus" in run_cmd and "vllm/vllm-openai:v0.33.0" in run_cmd


def test_launch_failed_when_container_exits():
    fake = FakeDocker(exit_early=True)
    r = launch(C, VllmAdapter(), run=fake, client=_client(503), wait_s=5, sleep=lambda s: None)
    assert r.status == "failed" and r.failure == "oom"
    assert ["docker", "rm", "-f", "cid123"] in fake.calls
```

Run: `uv run pytest tests/test_launch.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/launch/docker.py
import socket
import subprocess
import time

import httpx

from ttalkkak.engines.base import EngineAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import Candidate, FailureKind, Launched

_PATTERNS: list[tuple[FailureKind, tuple[str, ...]]] = [
    ("oom", ("out of memory", "no available memory for the cache blocks")),
    ("bad_arg", ("unrecognized arguments", "invalid choice", "error: argument")),
    ("download", ("hfhubhttperror", "repositorynotfound", "gatedrepo", "401", "403")),
]


def check_docker(run=subprocess.run) -> None:
    try:
        run(["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=15, check=True)
    except (FileNotFoundError, subprocess.SubprocessError) as err:
        raise TtalkkakError("docker 사용 불가: Docker를 설치하고 데몬을 켜 주세요", 3) from err


def free_port(preferred: int = 8000) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise TtalkkakError("빈 포트를 찾지 못했습니다", 3)


def classify_failure(logs: str, timed_out: bool) -> FailureKind:
    low = logs.lower()
    for kind, needles in _PATTERNS:
        if any(n in low for n in needles):
            return kind
    return "timeout" if timed_out else "unknown"


def _out(run, cmd: list[str]) -> str:
    return run(cmd, capture_output=True, text=True, timeout=60).stdout


def stop(container: str, run=subprocess.run) -> None:
    run(["docker", "rm", "-f", container], capture_output=True, text=True, timeout=60)


def launch(c: Candidate, adapter: EngineAdapter, run=subprocess.run,
           client: httpx.Client | None = None, wait_s: int = 900,
           sleep=time.sleep) -> Launched:
    client = client or httpx.Client()
    spec = adapter.build_args(c)
    port = free_port()
    cmd = ["docker", "run", "-d", "--gpus", "all", "--ipc=host",
           "-p", f"{port}:{spec.container_port}",
           "-v", "ttalkkak-hf-cache:/root/.cache/huggingface",
           "-e", "HF_TOKEN", *[f"-e{k}={v}" for k, v in spec.env.items()],
           spec.image, *spec.args]
    cid = _out(run, cmd).strip()
    base = f"http://127.0.0.1:{port}"
    deadline, timed_out = time.monotonic() + wait_s, True
    while time.monotonic() < deadline:
        if adapter.health(base, client):
            timed_out = False
            break
        if _out(run, ["docker", "inspect", "-f", "{{.State.Status}}", cid]).strip() != "running":
            timed_out = False
            break
        sleep(5)
    logs = _out(run, ["docker", "logs", cid])
    if adapter.health(base, client):
        models = client.get(f"{base}/v1/models", timeout=10).json()
        metrics = client.get(f"{base}/metrics", timeout=10).text
        return Launched(candidate_id=c.id, status="ready", base_url=base, container=cid,
                        logs=logs, metrics=metrics, models_json=models)
    stop(cid, run)
    return Launched(candidate_id=c.id, status="failed", container=cid,
                    failure=classify_failure(logs, timed_out),
                    log_tail=logs.splitlines()[-50:], logs=logs)
```

`launch/__init__.py`는 빈 파일. 컨테이너 시작 직후 `docker logs`가 빈 경우를 피하려고 health를 마지막에 한 번 더 본다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_launch.py -v` → Expected: 10 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/launch tests/test_launch.py
git commit -m "Launch candidates in Docker and classify failures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: OpenAI 클라이언트와 에이전트 적합성 검사

**Files:**
- Create: `src/ttalkkak/verify/__init__.py`, `src/ttalkkak/verify/openai_client.py`, `src/ttalkkak/verify/agent.py`, `tests/test_agent_checks.py`, `tests/mock_openai.py`

**Interfaces:**
- Consumes: `Requirements`, `CheckResult`
- Produces:
  - `ChatResult(content: str, reasoning: str, tool_calls: list[ToolCall])`, `ToolCall(name: str, arguments: str)`
  - `chat(client, base_url, model, messages, *, tools=None, stream=False, extra: dict | None = None, api_key: str | None = None) -> ChatResult`
  - `run_agent_checks(client, base_url, model, req: Requirements, api_key=None) -> list[CheckResult]`
  - 검사 이름: `tool_call_parsed`, `reasoning_not_in_content`, `stream_matches_nonstream`, `single_tool_call`
  - `tests/mock_openai.py`: `mock_transport(behavior: str) -> httpx.MockTransport` (`behavior` ∈ `good`, `think_leak`, `stream_mismatch`, `two_calls`, `split_args`)

- [ ] **Step 1: mock 서버 작성**

```python
# tests/mock_openai.py
import json

import httpx

WEATHER = {"name": "get_weather", "arguments": '{"city": "Seoul"}'}


def _sse(chunks: list[dict]) -> str:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


def _delta(**d):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def mock_transport(behavior: str) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        stream, tools = body.get("stream"), body.get("tools")
        calls = [WEATHER] if tools else []
        if behavior == "two_calls" and tools:
            calls = [WEATHER, {"name": "get_weather", "arguments": '{"city": "Busan"}'}]
        content = "" if calls else "Hello"
        if behavior == "think_leak":
            content = "<think>hmm</think>Hello"
        if not stream:
            msg = {"role": "assistant", "content": content,
                   "tool_calls": [{"id": f"c{i}", "type": "function", "function": c}
                                  for i, c in enumerate(calls)] or None}
            return httpx.Response(200, json={"choices": [{"index": 0, "message": msg}]})
        if behavior == "stream_mismatch":
            content, calls = "Different", []
        chunks = [_delta(role="assistant")]
        if content:
            chunks.append(_delta(content=content))
        for i, c in enumerate(calls):
            if behavior == "split_args":
                a = c["arguments"]
                chunks.append(_delta(tool_calls=[{"index": i, "id": f"c{i}", "type": "function",
                                                  "function": {"name": c["name"], "arguments": a[:5]}}]))
                chunks.append(_delta(tool_calls=[{"index": i, "function": {"arguments": a[5:]}}]))
            else:
                chunks.append(_delta(tool_calls=[{"index": i, "id": f"c{i}", "type": "function",
                                                  "function": c}]))
        chunks.append({"choices": [], "usage": {"completion_tokens": 3}})   # usage-only chunk
        return httpx.Response(200, text=_sse(chunks),
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)
```

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_agent_checks.py
import httpx

from mock_openai import mock_transport
from ttalkkak.schemas import Requirements
from ttalkkak.verify.agent import run_agent_checks
from ttalkkak.verify.openai_client import chat

ALL = Requirements(flags={"tool_calling": True, "reasoning": True, "streaming": True,
                          "single_tool_call": True})


def results(behavior, req=ALL):
    c = httpx.Client(transport=mock_transport(behavior))
    return {r.name: r.passed for r in run_agent_checks(c, "http://m/v1", "m", req)}


def test_good_server_passes_everything():
    assert all(results("good").values())


def test_detects_think_leak():
    assert results("think_leak")["reasoning_not_in_content"] is False


def test_detects_stream_mismatch():
    assert results("stream_mismatch")["stream_matches_nonstream"] is False


def test_detects_two_calls_when_single_required():
    assert results("two_calls")["single_tool_call"] is False


def test_stream_assembles_split_arguments_and_ignores_usage_chunk():   # Review Focus 5
    c = httpx.Client(transport=mock_transport("split_args"))
    r = chat(c, "http://m/v1", "m", [{"role": "user", "content": "x"}],
             tools=[{"type": "function", "function": {"name": "get_weather",
                                                      "parameters": {"type": "object"}}}],
             stream=True)
    assert [(t.name, t.arguments) for t in r.tool_calls] == [
        ("get_weather", '{"city": "Seoul"}')]


def test_only_requested_checks_run():
    names = results("good", Requirements(flags={"streaming": True}))
    assert set(names) == {"stream_matches_nonstream"}
```

Run: `uv run pytest tests/test_agent_checks.py -v` → Expected: FAIL. (`tests/`에 `conftest.py`가 없으면 `mock_openai` import를 위해 `tests/conftest.py`에 `import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parent))`를 추가한다.)

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/verify/openai_client.py
import json

import httpx
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    arguments: str


class ChatResult(BaseModel):
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def chat(client: httpx.Client, base_url: str, model: str, messages: list[dict], *,
         tools=None, stream=False, extra: dict | None = None,
         api_key: str | None = None) -> ChatResult:
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 256,
            "stream": stream} | (extra or {})
    if tools:
        body["tools"] = tools
    url = f"{base_url}/chat/completions"
    if not stream:
        msg = client.post(url, json=body, headers=_headers(api_key),
                          timeout=120).json()["choices"][0]["message"]
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
            tool_calls=[ToolCall(name=t["function"]["name"],
                                 arguments=t["function"].get("arguments") or "")
                        for t in msg.get("tool_calls") or []])
    content, reasoning, calls = [], [], {}
    with client.stream("POST", url, json=body, headers=_headers(api_key),
                       timeout=120) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            for choice in json.loads(data).get("choices") or []:
                d = choice.get("delta") or {}
                content.append(d.get("content") or "")
                reasoning.append(d.get("reasoning_content") or d.get("reasoning") or "")
                for t in d.get("tool_calls") or []:
                    slot = calls.setdefault(t["index"], {"name": "", "arguments": ""})
                    fn = t.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
    return ChatResult(content="".join(content), reasoning="".join(reasoning),
                      tool_calls=[ToolCall(**calls[i]) for i in sorted(calls)])
```

```python
# src/ttalkkak/verify/agent.py
import json

import httpx

from ttalkkak.schemas import CheckResult, Requirements
from ttalkkak.verify.openai_client import chat

WEATHER_TOOL = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]
ASK_WEATHER = [{"role": "user", "content": "What is the weather in Seoul? Use the tool."}]
ASK_TWO = [{"role": "user", "content": "Get the weather for Seoul and for Busan."}]
ASK_THINK = [{"role": "user", "content": "Is 91 prime? Answer yes or no."}]


def _valid_call(r) -> bool:
    if not r.tool_calls or r.tool_calls[0].name != "get_weather":
        return False
    try:
        return "city" in json.loads(r.tool_calls[0].arguments)
    except json.JSONDecodeError:
        return False


def run_agent_checks(client: httpx.Client, base_url: str, model: str,
                     req: Requirements, api_key: str | None = None) -> list[CheckResult]:
    f, out = req.flags, []
    tools = WEATHER_TOOL if f.get("tool_calling") else None
    if f.get("tool_calling"):
        r = chat(client, base_url, model, ASK_WEATHER, tools=tools, api_key=api_key)
        out.append(CheckResult(name="tool_call_parsed", passed=_valid_call(r),
                               detail=str([t.model_dump() for t in r.tool_calls])))
    if f.get("reasoning"):
        r = chat(client, base_url, model, ASK_THINK, api_key=api_key)
        leaked = "<think>" in r.content or "</think>" in r.content
        out.append(CheckResult(name="reasoning_not_in_content", passed=not leaked,
                               detail=r.content[:200]))
    if f.get("streaming"):
        msgs = ASK_WEATHER if tools else ASK_THINK
        a = chat(client, base_url, model, msgs, tools=tools, api_key=api_key)
        b = chat(client, base_url, model, msgs, tools=tools, stream=True, api_key=api_key)
        same = (a.content.strip() == b.content.strip()
                and [t.name for t in a.tool_calls] == [t.name for t in b.tool_calls])
        out.append(CheckResult(name="stream_matches_nonstream", passed=same,
                               detail=f"nonstream={a.model_dump()} stream={b.model_dump()}"))
    if f.get("single_tool_call"):
        r = chat(client, base_url, model, ASK_TWO, tools=WEATHER_TOOL,
                 extra={"parallel_tool_calls": False}, api_key=api_key)
        out.append(CheckResult(name="single_tool_call", passed=len(r.tool_calls) <= 1,
                               detail=f"{len(r.tool_calls)} calls"))
    return out
```

`temperature=0`이어도 엔진에 따라 스트림/비스트림 텍스트가 미세하게 다를 수 있다. Task 15 실측에서 정상 서버가 `stream_matches_nonstream`을 실패하면, 비교를 "tool call 이름·인자 일치 + 텍스트 앞 64자 일치"로 완화하고 그 사실을 리포트에 적는다(테스트도 같이 갱신).

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_agent_checks.py -v` → Expected: 6 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/verify tests/test_agent_checks.py tests/mock_openai.py tests/conftest.py
git commit -m "Add agent-readiness checks with a mock OpenAI server

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 정확도(lm_eval)와 처리량(guidellm) 래퍼

**Files:**
- Create: `src/ttalkkak/verify/accuracy.py`, `src/ttalkkak/verify/perf.py`, `tests/test_measure.py`, `tests/fixtures/lm_eval/results_sample.json`
- 사용: `tests/fixtures/guidellm/report_mock.json` (이미 커밋 전 복사됨: guidellm 0.9.0.dev0 mock 서버 실측 결과)

**Interfaces:**
- Produces:
  - `accuracy_cmd(base_url: str, model: str, out_dir: Path, limit: int = 100) -> list[str]`
  - `parse_accuracy(out_dir: Path) -> float`
  - `run_accuracy(base_url, model, out_dir, run=subprocess.run, limit=100) -> float`
  - `perf_cmd(base_url: str, model: str, out_file: Path, requests: int = 20) -> list[str]`
  - `parse_perf(report: dict) -> dict[str, float]` (키: `output_tok_s`, `ttft_ms`, `itl_ms`, `req_s`)
  - `run_perf(base_url, model, out_file, run=subprocess.run) -> dict[str, float]`

- [ ] **Step 1: lm_eval fixture 작성 (임시, Task 15에서 실측으로 교체)**

`tests/fixtures/lm_eval/results_sample.json`:
```json
{"_note": "SYNTHETIC shape of lm-eval 0.4.x results; replace with a real file (Task 15)",
 "results": {"gsm8k": {"alias": "gsm8k", "exact_match,strict-match": 0.62,
                       "exact_match,flexible-extract": 0.64}}}
```

- [ ] **Step 2: 실패하는 테스트**

```python
# tests/test_measure.py
import json
import shutil
from pathlib import Path

from ttalkkak.verify.accuracy import accuracy_cmd, parse_accuracy
from ttalkkak.verify.perf import parse_perf, perf_cmd

FIX = Path(__file__).parent / "fixtures"


def test_accuracy_cmd_targets_chat_completions(tmp_path):
    cmd = accuracy_cmd("http://h:8001/v1", "m", tmp_path, limit=100)
    joined = " ".join(cmd)
    assert "local-chat-completions" in joined and "--limit 100" in joined
    assert "base_url=http://h:8001/v1/chat/completions" in joined
    assert "--apply_chat_template" in cmd


def test_parse_accuracy_reads_nested_results_file(tmp_path):
    d = tmp_path / "m__x"
    d.mkdir()
    shutil.copy(FIX / "lm_eval/results_sample.json", d / "results_2026-11-05.json")
    assert parse_accuracy(tmp_path) == 0.64


def test_perf_cmd_shape(tmp_path):
    cmd = perf_cmd("http://h:8001/v1", "m", tmp_path / "p.json", requests=20)
    assert cmd[:2] == ["guidellm", "run"]
    assert "kind=openai_http,target=http://h:8001,model=m" in cmd
    assert "kind=max_requests,count=20" in cmd


def test_parse_perf_from_real_guidellm_report():
    m = parse_perf(json.loads((FIX / "guidellm/report_mock.json").read_text()))
    assert round(m["output_tok_s"], 1) == 17.7 and round(m["ttft_ms"]) == 106
```

Run: `uv run pytest tests/test_measure.py -v` → Expected: FAIL.

- [ ] **Step 3: 구현**

```python
# src/ttalkkak/verify/accuracy.py
import json
import subprocess
from pathlib import Path

from ttalkkak.errors import TtalkkakError

METRIC = "exact_match,flexible-extract"


def accuracy_cmd(base_url: str, model: str, out_dir: Path, limit: int = 100) -> list[str]:
    return ["lm_eval", "--model", "local-chat-completions",
            "--model_args", f"model={model},base_url={base_url}/chat/completions,"
                            "num_concurrent=4,max_retries=3,tokenized_requests=False",
            "--tasks", "gsm8k", "--limit", str(limit), "--apply_chat_template",
            "--seed", "1234", "--output_path", str(out_dir)]


def parse_accuracy(out_dir: Path) -> float:
    files = sorted(out_dir.rglob("results_*.json"))
    if not files:
        raise TtalkkakError(f"lm_eval 결과 파일이 없습니다: {out_dir}")
    return float(json.loads(files[-1].read_text())["results"]["gsm8k"][METRIC])


def run_accuracy(base_url, model, out_dir: Path, run=subprocess.run, limit=100) -> float:
    run(accuracy_cmd(base_url, model, out_dir, limit), check=True, timeout=3600)
    return parse_accuracy(out_dir)
```

```python
# src/ttalkkak/verify/perf.py
import json
import subprocess
from pathlib import Path


def perf_cmd(base_url: str, model: str, out_file: Path, requests: int = 20) -> list[str]:
    target = base_url.removesuffix("/v1")
    return ["guidellm", "run",
            "--backend", f"kind=openai_http,target={target},model={model}",
            "--profile", "kind=synchronous",
            "--constraint", f"kind=max_requests,count={requests}",
            "--data", "kind=synthetic_text,prompt_tokens=256,output_tokens=128",
            "--tokenizer", f"kind=hf_auto,model={model}",
            "--output", f"kind=json,path={out_file}",
            "--disable-console-interactive", "--disable-progress"]


def parse_perf(report: dict) -> dict[str, float]:
    m = report["benchmarks"][0]["metrics"]

    def mean(key):
        return float(m[key]["successful"]["mean"])

    return {"output_tok_s": mean("output_tokens_per_second"),
            "ttft_ms": mean("time_to_first_token_ms"),
            "itl_ms": mean("inter_token_latency_ms"),
            "req_s": mean("requests_per_second")}


def run_perf(base_url, model, out_file: Path, run=subprocess.run) -> dict[str, float]:
    run(perf_cmd(base_url, model, out_file), check=True, timeout=1800)
    return parse_perf(json.loads(out_file.read_text()))
```

- [ ] **Step 4: CLI 플래그 실물 확인**

Run: `uv run guidellm run --help | grep -E -- "--(backend|profile|constraint|data|tokenizer|output)"` 와 `uv run lm_eval --help | grep -E -- "--(model_args|apply_chat_template|limit|output_path|seed)"` → Expected: 모든 플래그가 존재. 없는 플래그는 위 명령 생성 함수와 테스트를 같이 고친다.

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/test_measure.py -v` → Expected: 4 passed.

- [ ] **Step 6: 커밋**

```bash
git add src/ttalkkak/verify/accuracy.py src/ttalkkak/verify/perf.py tests/test_measure.py tests/fixtures/lm_eval tests/fixtures/guidellm
git commit -m "Wrap lm_eval and guidellm for accuracy and perf

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 적용 비교와 후보 선택

**Files:**
- Create: `src/ttalkkak/verify/applied.py`, `src/ttalkkak/pick.py`, `tests/test_pick.py`

**Interfaces:**
- Consumes: `Candidate`, `AppliedConfig`, `CandidateVerdict`, `Verification`, `Pick`
- Produces:
  - `compare_applied(c: Candidate, applied: AppliedConfig) -> list[str]` (불일치 문장 목록. 값이 `None`(unknown)이면 불일치가 아니라 `"<key>: 확인 불가"`로 기록)
  - `ACCURACY_DROP_MAX = 0.05`
  - `pick(verdicts: list[CandidateVerdict], baseline: float | None, launched_urls: dict[str, str]) -> Pick`

선택 규칙(스펙 7): agent 검사 전부 통과 + (accuracy 검사가 있으면) `baseline - accuracy <= 0.05` → 그중 `perf["output_tok_s"]` 최대. 동률이면 id가 작은 것(c1이 메모리 여유가 큼). mismatch는 탈락 사유가 아니다(리포트 경고).

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_pick.py
from ttalkkak.pick import pick
from ttalkkak.schemas import (AppliedConfig, AppliedValue, Candidate, CandidateVerdict,
                              CheckResult)
from ttalkkak.verify.applied import compare_applied


def test_compare_reports_mismatch_and_unknown():
    c = Candidate(id="c1", engine="vllm", model_id="m",
                  args={"kv_cache_dtype": "fp8", "max_model_len": 16384,
                        "tool_call_parser": "hermes"})
    a = AppliedConfig(values={
        "kv_cache_dtype": AppliedValue(value="auto", evidence="metric"),
        "max_model_len": AppliedValue(value=16384, evidence="log"),
        "tool_call_parser": AppliedValue(value=None, evidence="unknown")})
    out = compare_applied(c, a)
    assert "kv_cache_dtype: 요청 fp8, 실제 auto (metric)" in out
    assert "tool_call_parser: 확인 불가" in out
    assert not any(s.startswith("max_model_len") for s in out)


def v(cid, tok_s, acc=None, agent_ok=True):
    return CandidateVerdict(candidate_id=cid, accuracy=acc, perf={"output_tok_s": tok_s},
                            agent=[CheckResult(name="x", passed=agent_ok)])


def test_fast_but_inaccurate_candidate_is_rejected():
    p = pick([v("c1", 30, 0.62), v("c2", 45, 0.40)], baseline=0.62,
             launched_urls={"c1": "u1", "c2": "u2"})
    assert p.chosen == "c1" and "정확도" in p.rejected["c2"] and p.base_url == "u1"


def test_agent_failure_rejects():
    p = pick([v("c1", 30, agent_ok=False), v("c2", 20)], baseline=None,
             launched_urls={"c1": "u1", "c2": "u2"})
    assert p.chosen == "c2" and "agent" in p.rejected["c1"]


def test_no_survivor():
    p = pick([v("c1", 30, agent_ok=False)], baseline=None, launched_urls={"c1": "u1"})
    assert p.chosen is None
```

Run: `uv run pytest tests/test_pick.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/verify/applied.py
from ttalkkak.schemas import AppliedConfig, Candidate

COMPARED = ("kv_cache_dtype", "max_model_len", "tool_call_parser", "reasoning_parser",
            "enable_auto_tool_choice")


def compare_applied(c: Candidate, applied: AppliedConfig) -> list[str]:
    out = []
    for key in COMPARED:
        if key not in c.args:
            continue
        got = applied.values.get(key)
        if got is None or got.value is None:
            out.append(f"{key}: 확인 불가")
        elif str(got.value) != str(c.args[key]):
            out.append(f"{key}: 요청 {c.args[key]}, 실제 {got.value} ({got.evidence})")
    return out
```

```python
# src/ttalkkak/pick.py
from ttalkkak.schemas import CandidateVerdict, Pick

ACCURACY_DROP_MAX = 0.05


def pick(verdicts: list[CandidateVerdict], baseline: float | None,
         launched_urls: dict[str, str]) -> Pick:
    rejected, ok = {}, []
    for v in verdicts:
        failed = [c.name for c in v.agent if not c.passed]
        if failed:
            rejected[v.candidate_id] = f"agent 검사 실패: {', '.join(failed)}"
        elif (v.accuracy is not None and baseline is not None
              and baseline - v.accuracy > ACCURACY_DROP_MAX):
            rejected[v.candidate_id] = (f"정확도 하락 {baseline - v.accuracy:.1%}p "
                                        f"(기준 {baseline:.2f} → {v.accuracy:.2f})")
        else:
            ok.append(v)
    if not ok:
        return Pick(chosen=None, rejected=rejected)
    # 동률이면 앞선 후보(c1, 메모리 여유가 큼)
    best = max(reversed(ok), key=lambda v: v.perf.get("output_tok_s", 0.0))
    return Pick(chosen=best.candidate_id, rejected=rejected,
                base_url=launched_urls.get(best.candidate_id))
```

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/test_pick.py -v` → Expected: 4 passed.

- [ ] **Step 4: 커밋**

```bash
git add src/ttalkkak/verify/applied.py src/ttalkkak/pick.py tests/test_pick.py
git commit -m "Compare applied config and pick a candidate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 리포트

**Files:**
- Create: `src/ttalkkak/report.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: `Plan`, `Launched`, `Verification`, `Pick`
- Produces: `render_report(plan: Plan, launched: list[Launched], ver: Verification, chosen: Pick) -> str`

리포트 구성(순서 고정): 제목과 선택 결과 → **경고**(mismatch, 확인 불가) → 요구사항과 출처 skill → 후보별 "예측 vs 실측" 표(max_model_len, KV 토큰, 출력 tok/s, TTFT, 정확도) → 탈락·실패 사유(실패면 분류 + 로그 마지막 10줄) → 에이전트 검사 결과 → LangGraph 연결 설정.

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_report.py
from ttalkkak.report import render_report
from ttalkkak.schemas import (AppliedConfig, AppliedValue, Candidate, CandidateVerdict,
                              Launched, Pick, Plan, Requirements, Verification)


def test_report_has_warning_table_and_failure():
    plan = Plan(requirements=Requirements(flags={"tool_calling": True},
                                          sources={"tool_calling": ["web-fetch"]}),
                candidates=[Candidate(id="c1", engine="vllm", model_id="m",
                                      args={"max_model_len": 8192},
                                      predicted={"kv_tokens_fit": 9000}),
                            Candidate(id="c2", engine="vllm", model_id="m", args={})])
    launched = [Launched(candidate_id="c1", status="ready", base_url="http://h:8000"),
                Launched(candidate_id="c2", status="failed", failure="oom",
                         log_tail=["CUDA out of memory"])]
    ver = Verification(verdicts=[CandidateVerdict(
        candidate_id="c1", mismatches=["kv_cache_dtype: 요청 fp8, 실제 auto (metric)"],
        applied=AppliedConfig(values={"kv_cache_tokens": AppliedValue(value=7000, evidence="log")}),
        perf={"output_tok_s": 30.0, "ttft_ms": 120.0})])
    md = render_report(plan, launched, ver, Pick(chosen="c1", base_url="http://h:8000"))
    assert md.index("경고") < md.index("예측 vs 실측")
    assert "kv_cache_dtype: 요청 fp8, 실제 auto" in md
    assert "| c1 | 8192 |" in md and "9000 / 7000" in md
    assert "oom" in md and "CUDA out of memory" in md
    assert "tool_calling ← web-fetch" in md
    assert "base_url=\"http://h:8000/v1\"" in md
```

Run: `uv run pytest tests/test_report.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/report.py
from ttalkkak.schemas import Launched, Pick, Plan, Verification


def _fmt(x) -> str:
    return "-" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x))


def render_report(plan: Plan, launched: list[Launched], ver: Verification,
                  chosen: Pick) -> str:
    by_v = {v.candidate_id: v for v in ver.verdicts}
    by_l = {l.candidate_id: l for l in launched}
    lines = ["# ttalkkak report", "",
             f"**선택:** {chosen.chosen or '없음 (모든 후보 탈락)'}", ""]
    warns = [f"- {c}: {m}" for c, v in by_v.items() for m in v.mismatches]
    lines += ["## 경고", *(warns or ["- 없음"]), ""]
    lines += ["## 요구사항", *[f"- {k} ← {', '.join(s)}"
                             for k, s in plan.requirements.sources.items()] or ["- 없음"], ""]
    lines += ["## 예측 vs 실측", "",
              "| 후보 | max_model_len | KV 토큰 예측 / 실측 | 출력 tok/s | TTFT ms | 정확도 |",
              "|---|---|---|---|---|---|"]
    for c in plan.candidates:
        v = by_v.get(c.id)
        real_kv = v.applied.values.get("kv_cache_tokens") if v else None
        lines.append(
            f"| {c.id} | {c.args.get('max_model_len', '-')} | "
            f"{_fmt(int(c.predicted['kv_tokens_fit']) if 'kv_tokens_fit' in c.predicted else None)}"
            f" / {_fmt(real_kv.value if real_kv else None)} | "
            f"{_fmt(v.perf.get('output_tok_s') if v else None)} | "
            f"{_fmt(v.perf.get('ttft_ms') if v else None)} | "
            f"{_fmt(v.accuracy if v else None)} |")
    lines += ["", "## 탈락·실패"]
    for cid, why in chosen.rejected.items():
        lines.append(f"- {cid}: {why}")
    for l in launched:
        if l.status == "failed":
            lines += [f"- {l.candidate_id}: 기동 실패 ({l.failure})", "```",
                      *l.log_tail[-10:], "```"]
    for c in plan.candidates:
        for b in c.blockers:
            lines.append(f"- {c.id}: 계획 단계에서 제외 ({b})")
    lines += ["", "## 에이전트 검사"]
    for cid, v in by_v.items():
        for r in v.agent:
            lines.append(f"- {cid} {r.name}: {'통과' if r.passed else '실패'}")
    if chosen.base_url:
        model = next((c.model_id for c in plan.candidates if c.id == chosen.chosen), "")
        lines += ["", "## 연결", "```python",
                  "from langchain_openai import ChatOpenAI",
                  f"llm = ChatOpenAI(base_url=\"{chosen.base_url}/v1\", api_key=\"EMPTY\", "
                  f"model=\"{model}\")", "```"]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/test_report.py -v` → Expected: 1 passed.

- [ ] **Step 4: 커밋**

```bash
git add src/ttalkkak/report.py tests/test_report.py
git commit -m "Render the prediction vs measurement report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: 파이프라인과 `ttalkkak up`

**Files:**
- Create: `src/ttalkkak/pipeline.py`, `tests/test_pipeline.py`
- Modify: `src/ttalkkak/cli.py`

**Interfaces:**
- Consumes: Task 2~11의 모든 공개 함수
- Produces:
  - `Deps` dataclass: `probe`, `check_docker`, `launch`, `stop`, `client`, `run_accuracy`, `run_perf` (테스트에서 가짜로 교체)
  - `run_up(model: str, skills: list[str], runs_root: Path, skills_root: Path, deps: Deps, resume: str | None = None) -> tuple[Path, Pick]`
  - CLI: `ttalkkak up --model M --skills a,b [--resume RUN] [--keep-all]`, 종료 코드: 성공 0, 후보 없음 1, 입력/접근 오류 2, docker 오류 3

동작:
1. `resume`이 있으면 `runs_root/resume`의 `state.json`을 읽고 probe·plan이 `done`에 있으면 저장된 JSON을 읽어 건너뛴다. launch 이후 단계는 컨테이너가 이미 정리됐으므로 항상 다시 실행한다. 없으면 `runs_root/<YYYYmmdd-HHMMSS>/`를 만든다.
2. 로컬 모델이면 `check_docker()`를 probe보다 먼저 호출(Review Focus 2).
3. NIM(`nim:`)이면 plan/launch를 건너뛰고 `base_url="https://integrate.api.nvidia.com/v1"`, `api_key=$NVIDIA_API_KEY`로 accuracy와 agent만 실행. 키가 없으면 `TtalkkakError(exit_code=2)`.
4. blocker 없는 후보만 launch. 전부 blocker면 report를 쓰고 종료 코드 1.
5. accuracy 기준선: c1(KV auto) 점수를 `baseline_accuracy`로 쓴다. c1이 실패하면 baseline 없음(fp8 후보는 `requires_accuracy`이므로 baseline 없이 선택 불가 → rejected "기준 정확도 없음").
6. 선택되지 않은 후보 컨테이너는 `stop`. `--keep-all`이면 유지.
7. `KeyboardInterrupt`나 예외 시 launch된 컨테이너를 모두 `stop`하고 state를 저장한 뒤 다시 raise.

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_pipeline.py
import json
from pathlib import Path

import httpx
import pytest

from mock_openai import mock_transport
from ttalkkak.pipeline import Deps, run_up
from ttalkkak.schemas import GpuInfo, Launched, ModelInfo, ProbeResult

M = ModelInfo(model_id="Qwen/Qwen2.5-1.5B-Instruct", family="qwen2", num_layers=28,
              num_kv_heads=2, head_dim=128, dtype="bfloat16", quantization=None,
              max_position=32768, weight_bytes=3_100_000_000)
G = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")
SKILLS = Path(__file__).parent / "fixtures/skills"


def deps(stopped, launch_status="ready", interrupt=False):
    def launch(c, adapter, **kw):
        if interrupt:
            raise KeyboardInterrupt
        return Launched(candidate_id=c.id, status=launch_status, container=f"k{c.id}",
                        base_url=f"http://h/{c.id}", models_json={}, logs="", metrics="")
    return Deps(probe=lambda m: ProbeResult(model=M, gpu=G), check_docker=lambda: None,
                launch=launch, stop=lambda cid: stopped.append(cid),
                client=httpx.Client(transport=mock_transport("good")),
                run_accuracy=lambda url, model, out: 0.6,
                run_perf=lambda url, model, out: {"output_tok_s": 10.0 if "/c1/" in url else 20.0})


def test_up_writes_all_stage_files_and_keeps_chosen(tmp_path):
    stopped = []
    run_dir, p = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped))
    for f in ["probe.json", "plan.json", "launch.json", "verify.json", "pick.json",
              "report.md", "state.json"]:
        assert (run_dir / f).exists(), f
    assert p.chosen == "c2" and stopped == ["kc1"]


def test_interrupt_cleans_up_and_saves_state(tmp_path):
    stopped = []
    with pytest.raises(KeyboardInterrupt):
        run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped, interrupt=True))
    state = json.loads(next(tmp_path.glob("*/state.json")).read_text())
    assert state["done"] == ["probe", "plan"]


def test_resume_skips_done_stages(tmp_path):
    stopped = []
    run_dir, _ = run_up(M.model_id, ["tooly"], tmp_path, SKILLS, deps(stopped))
    calls = []
    d = deps([])
    d.probe = lambda m: calls.append(m)
    run_up(M.model_id, ["tooly"], tmp_path, SKILLS, d, resume=run_dir.name)
    assert calls == []
```

`tests/fixtures/skills/tooly/SKILL.md`: `---\nname: tooly\ndescription: test skill\n---\nUse tools.\n`, `tests/fixtures/skills/tooly/ttalkkak.yaml`: `requires:\n  tool_calling: true\n  streaming: true\nmin_context: 4096\n`.

Run: `uv run pytest tests/test_pipeline.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/pipeline.py
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from pydantic import BaseModel

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.errors import TtalkkakError
from ttalkkak.pick import pick
from ttalkkak.plan.candidates import make_plan
from ttalkkak.report import render_report
from ttalkkak.schemas import (CandidateVerdict, Launched, Pick, Plan, ProbeResult,
                              Requirements, RunState, Verification)
from ttalkkak.skills.loader import load_skills
from ttalkkak.skills.requirements import merge_requirements
from ttalkkak.verify.agent import run_agent_checks
from ttalkkak.verify.applied import compare_applied

NIM_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class Deps:
    probe: Callable[[str], ProbeResult]
    check_docker: Callable[[], None]
    launch: Callable[..., Launched]
    stop: Callable[[str], None]
    client: httpx.Client
    run_accuracy: Callable[[str, str, Path], float]
    run_perf: Callable[[str, str, Path], dict[str, float]]


class _LaunchList(BaseModel):
    items: list[Launched]


def _save(run_dir: Path, name: str, obj: BaseModel) -> None:
    (run_dir / name).write_text(obj.model_dump_json(indent=2), encoding="utf-8")


def _load(run_dir: Path, name: str, cls):
    return cls.model_validate_json((run_dir / name).read_text(encoding="utf-8"))


def run_up(model: str, skills: list[str], runs_root: Path, skills_root: Path, deps: Deps,
           resume: str | None = None, keep_all: bool = False) -> tuple[Path, Pick]:
    run_dir = runs_root / (resume or time.strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    state = (_load(run_dir, "state.json", RunState) if resume
             else RunState(run_id=run_dir.name))
    req = merge_requirements(load_skills(skills_root, skills))
    remote = model.startswith("nim:")
    launched: list[Launched] = []

    def mark(stage):
        state.done.append(stage)
        _save(run_dir, "state.json", state)

    try:
        if not remote and "probe" not in state.done:
            deps.check_docker()
        if "probe" in state.done:
            pr = _load(run_dir, "probe.json", ProbeResult)
        else:
            pr = deps.probe(model)
            _save(run_dir, "probe.json", pr)
            mark("probe")
        if remote:
            return _run_remote(run_dir, model, req, deps)
        if "plan" in state.done:
            plan = _load(run_dir, "plan.json", Plan)
        else:
            plan = make_plan(pr, req)
            _save(run_dir, "plan.json", plan)
            mark("plan")
        runnable = [c for c in plan.candidates if not c.blockers]
        adapter = VllmAdapter()
        for c in runnable:
            launched.append(deps.launch(c, adapter))
        _save(run_dir, "launch.json", _LaunchList(items=launched))
        mark("launch")
        verdicts, baseline = [], None
        for c in runnable:
            l = next(x for x in launched if x.candidate_id == c.id)
            if l.status != "ready":
                continue
            applied = adapter.read_applied(l.logs, l.metrics, l.models_json)
            v = CandidateVerdict(candidate_id=c.id, applied=applied,
                                 mismatches=compare_applied(c, applied))
            v.agent = run_agent_checks(deps.client, f"{l.base_url}/v1", c.model_id, req)
            v.accuracy = deps.run_accuracy(f"{l.base_url}/v1", c.model_id,
                                           run_dir / f"acc_{c.id}")
            if c.args.get("kv_cache_dtype") == "auto":
                baseline = v.accuracy
            v.perf = deps.run_perf(f"{l.base_url}/v1", c.model_id, run_dir / f"perf_{c.id}.json")
            verdicts.append(v)
        ver = Verification(verdicts=verdicts, baseline_accuracy=baseline)
        _save(run_dir, "verify.json", ver)
        mark("verify")
        urls = {l.candidate_id: l.base_url for l in launched if l.base_url}
        chosen = pick(verdicts, baseline, urls)
        needs_base = {c.id for c in runnable if c.requires_accuracy}
        if baseline is None and chosen.chosen in needs_base:
            chosen.rejected[chosen.chosen] = "기준 정확도 없음"
            chosen = pick([v for v in verdicts if v.candidate_id not in needs_base],
                          None, urls)
        _save(run_dir, "pick.json", chosen)
        mark("pick")
        (run_dir / "report.md").write_text(render_report(plan, launched, ver, chosen),
                                           encoding="utf-8")
        for l in launched:
            if l.container and l.status == "ready" and l.candidate_id != chosen.chosen \
                    and not keep_all:
                deps.stop(l.container)
        return run_dir, chosen
    except BaseException:
        for l in launched:
            if l.container:
                deps.stop(l.container)
        _save(run_dir, "state.json", state)
        raise


def _run_remote(run_dir: Path, model: str, req: Requirements, deps: Deps):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise TtalkkakError("NIM 모델에는 NVIDIA_API_KEY 환경변수가 필요합니다", 2)
    name = model.removeprefix("nim:")
    v = CandidateVerdict(candidate_id="nim")
    v.agent = run_agent_checks(deps.client, NIM_URL, name, req, api_key=key)
    ver = Verification(verdicts=[v])
    _save(run_dir, "verify.json", ver)
    chosen = pick([v], None, {"nim": NIM_URL.removesuffix("/v1")})
    _save(run_dir, "pick.json", chosen)
    return run_dir, chosen
```

NIM 경로의 accuracy는 1단계에서 제외한다(lm_eval이 인증 헤더를 다루는 방식 확인 전). 이 결정을 Task 15 리포트 메모에 적는다.

`src/ttalkkak/cli.py`에 추가:

```python
import subprocess
from pathlib import Path

import httpx
import typer

from ttalkkak.errors import TtalkkakError
from ttalkkak.launch.docker import check_docker, launch, stop
from ttalkkak.pipeline import Deps, run_up
from ttalkkak.probe import probe
from ttalkkak.verify.accuracy import run_accuracy
from ttalkkak.verify.perf import run_perf

app = typer.Typer(no_args_is_help=True)
ROOT = Path.cwd()


@app.command()
def up(model: str = typer.Option(...), skills: str = typer.Option(""),
       resume: str | None = None, keep_all: bool = False) -> None:
    deps = Deps(probe=probe, check_docker=check_docker,
                launch=lambda c, a: launch(c, a), stop=stop, client=httpx.Client(),
                run_accuracy=run_accuracy, run_perf=run_perf)
    try:
        run_dir, chosen = run_up(model, [s for s in skills.split(",") if s],
                                 ROOT / "runs", ROOT / "skills", deps, resume, keep_all)
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code)
    typer.echo((run_dir / "report.md").read_text(encoding="utf-8")
               if (run_dir / "report.md").exists() else f"run: {run_dir}")
    raise typer.Exit(0 if chosen.chosen else 1)
```

(Task 1의 `@app.callback()`은 유지.)

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -v` → Expected: 전체 통과(Task 1~12).

- [ ] **Step 4: 커밋**

```bash
git add src/ttalkkak/pipeline.py src/ttalkkak/cli.py tests/test_pipeline.py tests/fixtures/skills
git commit -m "Wire stages into ttalkkak up with resume and cleanup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: 에이전트 런타임과 `ttalkkak chat`

**Files:**
- Create: `src/ttalkkak/agent/__init__.py`, `src/ttalkkak/agent/runtime.py`, `tests/test_runtime.py`
- Modify: `src/ttalkkak/cli.py`

**Interfaces:**
- Consumes: `Skill`, `load_skills`, `Pick`
- Produces:
  - `system_prompt(skills: list[Skill]) -> str`
  - `mcp_config(skills: list[Skill]) -> dict` (`{"mcpServers": {name: {"command": cmd[0], "args": cmd[1:]}}}`)
  - `async build_agent(base_url: str, model: str, skills: list[Skill], api_key: str = "EMPTY")` → `(agent, adapter)`; `adapter`는 `async with`로 열고 닫는다.
  - CLI: `ttalkkak chat --run RUN --skills a,b`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_runtime.py
from pathlib import Path

from ttalkkak.agent.runtime import mcp_config, system_prompt
from ttalkkak.skills.loader import load_skill

SK = Path(__file__).parent / "fixtures/skills"


def test_system_prompt_includes_each_skill_body():
    p = system_prompt([load_skill(SK / "tooly")])
    assert "## skill: tooly" in p and "Use tools." in p


def test_mcp_config_shape(tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: fx\ndescription: d\n---\nb\n")
    (d / "ttalkkak.yaml").write_text(
        "mcp_servers:\n  - name: fetch\n    command: [uvx, mcp-server-fetch]\n")
    assert mcp_config([load_skill(d)]) == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
```

Run: `uv run pytest tests/test_runtime.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/agent/runtime.py
import warnings

from langchain.agents import create_agent
from langchain_core._api import LangChainBetaWarning
from langchain_openai import ChatOpenAI

from ttalkkak.skills.loader import Skill


def system_prompt(skills: list[Skill]) -> str:
    parts = ["You are a helpful agent. Follow the skill instructions below."]
    for s in skills:
        parts += [f"## skill: {s.name}", s.description, s.body.strip()]
    return "\n\n".join(parts)


def mcp_config(skills: list[Skill]) -> dict:
    servers = {m.name: {"command": m.command[0], "args": m.command[1:]}
               for s in skills for m in s.ext.mcp_servers}
    return {"mcpServers": servers}


async def build_agent(base_url: str, model: str, skills: list[Skill],
                      api_key: str = "EMPTY"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangChainBetaWarning)
        from langchain.mcp import MCPAdapter
    cfg = mcp_config(skills)
    adapter = MCPAdapter(cfg) if cfg["mcpServers"] else None
    tools = []
    if adapter:
        await adapter.__aenter__()
        tools = await adapter.list_tools()
    llm = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0)
    return create_agent(llm, tools, system_prompt=system_prompt(skills)), adapter
```

`cli.py`에 추가:

```python
import asyncio
import json


@app.command()
def chat(run: str = typer.Option(...), skills: str = typer.Option("")) -> None:
    from ttalkkak.agent.runtime import build_agent
    from ttalkkak.schemas import Pick, Plan
    from ttalkkak.skills.loader import load_skills
    rd = ROOT / "runs" / run
    chosen = Pick.model_validate_json((rd / "pick.json").read_text())
    if not chosen.base_url:
        raise typer.Exit(1)
    plan = Plan.model_validate_json((rd / "plan.json").read_text())
    model = next(c.model_id for c in plan.candidates if c.id == chosen.chosen)
    sk = load_skills(ROOT / "skills", [s for s in skills.split(",") if s])

    async def loop():
        agent, adapter = await build_agent(f"{chosen.base_url}/v1", model, sk)
        msgs = []
        try:
            while (text := input("> ").strip()) not in ("", "exit"):
                msgs.append({"role": "user", "content": text})
                out = await agent.ainvoke({"messages": msgs})
                msgs = out["messages"]
                print(msgs[-1].content)
        finally:
            if adapter:
                await adapter.__aexit__(None, None, None)
    asyncio.run(loop())
```

- [ ] **Step 3: `create_agent`/`MCPAdapter` 실물 확인**

Run: `uv run python -c "import inspect,langchain.agents as a;print(inspect.signature(a.create_agent))"` → Expected: 첫 인자 model, 둘째 tools, 키워드 `system_prompt` 존재. 다르면 `build_agent` 호출을 실제 시그니처에 맞춘다.
Run: `uv run python -c "import langchain.mcp as m;print(m.MCPAdapter)"` → Expected: 클래스 출력(beta 경고 1회). ImportError면 `uv add "langchain[mcp]==1.4.9"` 후 다시.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_runtime.py -v` → Expected: 2 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/ttalkkak/agent src/ttalkkak/cli.py tests/test_runtime.py pyproject.toml uv.lock
git commit -m "Add LangGraph agent runtime with skills and MCP tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: 기본 skill 3개와 내장 MCP 서버

**Files:**
- Create: `skills/web-fetch/{SKILL.md,ttalkkak.yaml}`, `skills/code-exec/{SKILL.md,ttalkkak.yaml}`, `skills/file-qa/{SKILL.md,ttalkkak.yaml}`, `src/ttalkkak/mcp_servers/__init__.py`, `src/ttalkkak/mcp_servers/pyexec.py`, `src/ttalkkak/mcp_servers/files.py`, `tests/test_builtin_skills.py`

**Interfaces:**
- Consumes: `load_skill`, fastmcp `FastMCP`
- Produces: `pyexec.run_python(code: str, timeout_s: int = 10, run=subprocess.run) -> str`, `files.read_text(path: str, root: Path) -> str`, `files.list_dir(path: str, root: Path) -> list[str]` (root 밖 경로는 `ValueError`)

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_builtin_skills.py
import subprocess
from pathlib import Path

import pytest

from ttalkkak.mcp_servers.files import list_dir, read_text
from ttalkkak.mcp_servers.pyexec import run_python
from ttalkkak.skills.loader import load_skill

ROOT = Path(__file__).parents[1] / "skills"


@pytest.mark.parametrize("name", ["web-fetch", "code-exec", "file-qa"])
def test_builtin_skills_load(name):
    s = load_skill(ROOT / name)
    assert s.ext.mcp_servers


def test_pyexec_runs_in_docker_without_network():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="2\n", stderr="")
    assert run_python("print(1+1)", run=run) == "2\n"
    assert seen["cmd"][:4] == ["docker", "run", "--rm", "--network=none"]


def test_files_refuse_escape(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    assert read_text("a.txt", tmp_path) == "hi"
    assert list_dir(".", tmp_path) == ["a.txt"]
    with pytest.raises(ValueError):
        read_text("../etc/passwd", tmp_path)
```

Run: `uv run pytest tests/test_builtin_skills.py -v` → Expected: FAIL.

- [ ] **Step 2: 구현**

```python
# src/ttalkkak/mcp_servers/pyexec.py
import subprocess

from fastmcp import FastMCP

mcp = FastMCP("pyexec")


def run_python(code: str, timeout_s: int = 10, run=subprocess.run) -> str:
    cmd = ["docker", "run", "--rm", "--network=none", "--memory=256m", "--cpus=1",
           "python:3.12-slim", "python", "-c", code]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s + 20)
    except subprocess.TimeoutExpired:
        return "error: timeout"
    return p.stdout + (f"\nstderr:\n{p.stderr}" if p.stderr else "")


@mcp.tool()
def python(code: str) -> str:
    """Run Python code in an isolated container (no network) and return its output."""
    return run_python(code)


if __name__ == "__main__":
    mcp.run()
```

```python
# src/ttalkkak/mcp_servers/files.py
import os
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("files")
ROOT = Path(os.environ.get("TTALKKAK_FILES_ROOT", ".")).resolve()


def _inside(path: str, root: Path) -> Path:
    p = (root / path).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"path outside root: {path}")
    return p


def read_text(path: str, root: Path = ROOT) -> str:
    return _inside(path, root).read_text(encoding="utf-8", errors="replace")[:200_000]


def list_dir(path: str, root: Path = ROOT) -> list[str]:
    return sorted(p.name for p in _inside(path, root).iterdir())


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file under the configured root."""
    return read_text(path)


@mcp.tool()
def list_files(path: str = ".") -> list[str]:
    """List entries of a directory under the configured root."""
    return list_dir(path)


if __name__ == "__main__":
    mcp.run()
```

skill 파일:

```markdown
<!-- skills/web-fetch/SKILL.md -->
---
name: web-fetch
description: Fetch a web page by URL and answer from its content with the URL cited. Use when the user gives a URL or asks about a specific page.
---
1. Call the fetch tool with the exact URL.
2. Answer only from the fetched text and cite the URL.
3. If the fetch fails, say so instead of guessing.
```
```yaml
# skills/web-fetch/ttalkkak.yaml
requires: {tool_calling: true, streaming: true}
min_context: 16384
mcp_servers:
  - {name: fetch, command: [uvx, mcp-server-fetch]}
```
```markdown
<!-- skills/code-exec/SKILL.md -->
---
name: code-exec
description: Run short Python snippets in an isolated container to compute exact answers. Use for arithmetic, data checks, or verifying code.
---
1. Write a minimal Python snippet that prints the result.
2. Call the python tool and report the printed output.
3. Never claim a result you did not get from the tool.
```
```yaml
# skills/code-exec/ttalkkak.yaml
requires: {tool_calling: true, structured_output: true}
min_context: 8192
mcp_servers:
  - {name: pyexec, command: [uv, run, python, -m, ttalkkak.mcp_servers.pyexec]}
```
```markdown
<!-- skills/file-qa/SKILL.md -->
---
name: file-qa
description: Answer questions about local text files under a configured folder. Use when the user asks about documents in that folder.
---
1. List files, then read only the files you need.
2. Quote the relevant lines and name the file.
```
```yaml
# skills/file-qa/ttalkkak.yaml
requires: {tool_calling: true}
min_context: 32768
mcp_servers:
  - {name: files, command: [uv, run, python, -m, ttalkkak.mcp_servers.files]}
```

`mcp_servers/__init__.py`는 빈 파일.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -v` → Expected: 전체 통과.

- [ ] **Step 4: 커밋**

```bash
git add skills src/ttalkkak/mcp_servers tests/test_builtin_skills.py
git commit -m "Add web-fetch, code-exec and file-qa skills with built-in MCP servers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: 3060 Ti end-to-end와 실측 fixture 교체 (사람이 데스크탑에서 실행)

**Files:**
- Modify: `tests/fixtures/vllm/startup_sample.log`, `tests/fixtures/vllm/metrics_sample.txt`, `tests/fixtures/lm_eval/results_sample.json` (실측으로 교체), 필요 시 `src/ttalkkak/engines/vllm.py`, `src/ttalkkak/data/parsers.yaml`, `src/ttalkkak/verify/agent.py`
- Create: `runs/e2e-3060ti/` (결과 커밋), `docs/notes/2026-xx-xx-e2e-3060ti.md`

- [ ] **Step 1: 데스크탑 준비** — Windows 데스크탑에서 저장소를 받고(`git clone` 또는 복사), `uv sync --all-extras`, `docker info` 확인, `HF_TOKEN` 설정.

- [ ] **Step 2: 작은 모델로 첫 실행**

Run: `uv run ttalkkak up --model Qwen/Qwen2.5-1.5B-Instruct --skills code-exec`
Expected: 종료 코드 0, `runs/<id>/report.md`에 후보 2개, c1·c2 모두 기동, 경고 섹션 존재.

- [ ] **Step 3: fixture 교체**

`docker logs <c1 컨테이너>`와 `curl http://127.0.0.1:<port>/metrics` 결과를 각각 `startup_sample.log`, `metrics_sample.txt`로 저장(첫 줄의 SYNTHETIC 표시 삭제). `runs/<id>/acc_c1/**/results_*.json`을 `results_sample.json`으로 복사. `uv run pytest -v`를 돌려 실패하는 기대값은 실측 값으로 고친다(정규식이 실제 줄과 안 맞으면 `vllm.py`를 고친다).

- [ ] **Step 4: 7B AWQ + skill 3개**

Run: `uv run ttalkkak up --model Qwen/Qwen2.5-7B-Instruct-AWQ --skills web-fetch,code-exec,file-qa`
Expected: file-qa의 32k 요구 때문에 c1이 계획 단계에서 막히고 c2(fp8)가 후보가 되거나, 둘 다 막혀 "file-qa의 min_context 32768 때문에 불가"가 리포트에 나온다. 어느 쪽이든 리포트의 예측 KV 토큰과 실측 KV 토큰을 노트에 기록.

- [ ] **Step 5: accuracy 기준 편차 측정**

같은 모델(1.5B)·c1 설정으로 `run_accuracy`를 3회 실행하고 점수 3개를 노트에 적는다. 최대-최소 편차가 5%p 이상이면 `ACCURACY_DROP_MAX`와 스펙 7절 값을 편차에 맞게 갱신(스펙에 근거 숫자 기록).

- [ ] **Step 6: `ttalkkak chat` 확인** — Run: `uv run ttalkkak chat --run <id> --skills code-exec`, 질문 "17*23을 계산해 줘" → Expected: python 도구 호출 후 391.

- [ ] **Step 7: 커밋**

```bash
git add tests/fixtures runs/e2e-3060ti docs/notes src
git commit -m "Record 3060 Ti end-to-end run and real fixtures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review 결과

- 스펙 커버리지: 1절 CLI(Task 12), 2절 차별점 1~5(Task 6·8·9·10·11), 4.1 단계 파일(Task 12), 4.2 어댑터(Task 6), 4.3 Docker(Task 7), 5절 skill(Task 2·13·14), 6절 매핑·메모리(Task 4·5), 7절 검증·선택(Task 8~10), 8절 에이전트(Task 13), 9절 오류(Task 7·12), 10절 테스트(각 Task + Task 15). 스펙 6.4의 양자화본 자동 검색은 1단계에서 리포트 안내로만 남겼다(Task 5에 명시).
- 스펙과 달라진 점: skill `web-search` → `web-fetch`(사용 MCP 서버가 검색이 아니라 가져오기), NIM 경로 accuracy 제외(Task 12에 명시). 둘 다 스펙 리뷰 때 사용자에게 알린다.
- 임시 fixture 세 개(`startup_sample.log`, `metrics_sample.txt`, `results_sample.json`)는 SYNTHETIC 표시가 있고 Task 15에서 실측으로 교체한다. guidellm fixture는 실측(mock 서버 대상)이다.
