from typing import Literal

from pydantic import BaseModel, Field

Flag = Literal[
    "tool_calling", "reasoning", "streaming", "structured_output", "single_tool_call"
]
FailureKind = Literal["oom", "bad_arg", "download", "timeout", "unknown"]


class GpuInfo(BaseModel):
    name: str
    memory_mib: int
    compute_capability: str
    vendor: str = "nvidia"
    chip: str = "nvidia-gpu"
    arch: str = ""


class ModelInfo(BaseModel):
    model_id: str
    family: str
    num_layers: int
    num_kv_heads: int
    head_dim: int
    dtype: str
    quantization: str | None
    max_position: int
    weight_bytes: int


class ProbeResult(BaseModel):
    model: ModelInfo | None
    gpu: GpuInfo | None
    remote: str | None = None


class Requirements(BaseModel):
    flags: dict[Flag, bool] = Field(default_factory=dict)
    min_context: int = 0
    sources: dict[str, list[str]] = Field(default_factory=dict)


class Candidate(BaseModel):
    id: str
    engine: Literal["vllm", "sglang"]
    model_id: str
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)
    predicted: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    requires_accuracy: bool = False
    baseline_only: bool = False  # 정확도 기준선만 재고 선택 대상이 아님


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
    collected: dict = Field(default_factory=dict)


class AppliedValue(BaseModel):
    value: str | int | float | bool | None
    evidence: str


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
    rejected: dict[str, str] = Field(default_factory=dict)
    base_url: str | None = None
    model: str | None = None


class RunState(BaseModel):
    run_id: str
    done: list[str] = Field(default_factory=list)
