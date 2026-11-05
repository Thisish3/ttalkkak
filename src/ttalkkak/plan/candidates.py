from ttalkkak.errors import TtalkkakError
from ttalkkak.plan.memory import kv_bytes_per_token, max_context_fit
from ttalkkak.plan.parsers import parsers_for
from ttalkkak.schemas import Candidate, Plan, ProbeResult, Requirements

BASELINE_LEN = 4096  # gsm8k 프롬프트 + 생성이 들어가는 길이


def _parser_args(family: str, req: Requirements, engine: str) -> tuple[dict, list[str]]:
    args: dict = {}
    blockers: list[str] = []
    entry = parsers_for(family, engine)
    if req.flags.get("tool_calling"):
        if entry and entry.tool:
            args["tool_call_parser"] = entry.tool
            if engine == "vllm":
                args["enable_auto_tool_choice"] = True
        else:
            who = ", ".join(req.sources.get("tool_calling", [])) or "요청"
            blockers.append(
                f"no tool parser known for {family} on {engine} (needed by {who})"
            )
    if req.flags.get("reasoning"):
        if entry and entry.reasoning:
            args["reasoning_parser"] = entry.reasoning
        else:
            who = ", ".join(req.sources.get("reasoning", [])) or "요청"
            blockers.append(
                f"no reasoning parser known for {family} on {engine} (needed by {who})"
            )
    return args, blockers


def make_plan(probe: ProbeResult, req: Requirements) -> Plan:
    m, g = probe.model, probe.gpu
    if m is None or g is None:
        raise TtalkkakError("로컬 서빙 계획에는 HF 모델 정보와 GPU가 필요합니다", 2)
    parser_args, parser_blockers = _parser_args(m.family, req, "vllm")
    out = []
    for i, kv in enumerate(["auto", "fp8"], start=1):
        fit = max_context_fit(m, g, kv)
        length = min(m.max_position, fit)
        blockers = list(parser_blockers)
        per_token = kv_bytes_per_token(m, kv)
        reasons = [f"KV {kv}: {per_token} B/token, 들어가는 문맥 약 {fit} 토큰"]
        if length < max(req.min_context, 1):
            blockers.append(f"max_model_len {length} < 필요 {req.min_context}")
            who = ", ".join(req.sources.get("min_context", [])) or "요청"
            reasons.append(
                f"{who}의 min_context {req.min_context} 때문에 불가. "
                "양자화본 또는 더 작은 모델 필요"
            )
        out.append(
            Candidate(
                id=f"c{i}",
                engine="vllm",
                model_id=m.model_id,
                args={
                    "kv_cache_dtype": kv,
                    "max_model_len": length,
                    "gpu_memory_utilization": 0.9,
                }
                | parser_args,
                predicted={
                    "weight_gib": m.weight_bytes / 2**30,
                    "kv_tokens_fit": float(fit),
                },
                reasons=reasons,
                blockers=blockers,
                requires_accuracy=(kv == "fp8"),
            )
        )
    out.append(_sglang_candidate(probe, req))
    c1 = out[0]
    fp8_runnable = any(not c.blockers for c in out[1:])
    if c1.blockers and fp8_runnable and c1.args["max_model_len"] >= BASELINE_LEN:
        out.insert(
            0,
            c1.model_copy(
                update={
                    "id": "c0",
                    "args": c1.args | {"max_model_len": BASELINE_LEN},
                    "blockers": [],
                    "reasons": ["정확도 기준선 전용: KV auto, 짧은 문맥"],
                    "baseline_only": True,
                }
            ),
        )
    return Plan(candidates=out, requirements=req)


def _sglang_candidate(probe: ProbeResult, req: Requirements) -> Candidate:
    m, g = probe.model, probe.gpu
    need = max(req.min_context, 1)
    s_args, s_blockers = _parser_args(m.family, req, "sglang")
    auto_len = min(m.max_position, max_context_fit(m, g, "auto"))
    kv = "auto" if auto_len >= need else "fp8"
    fit = max_context_fit(m, g, kv)
    length = min(m.max_position, fit)
    blockers = list(s_blockers)
    reasons = [
        f"SGLang KV {kv}: 들어가는 문맥 약 {fit} 토큰 (근사: 메모리 비율은 SGLang 기본값)"
    ]
    if length < need:
        blockers.append(f"max_model_len {length} < 필요 {req.min_context}")
        who = ", ".join(req.sources.get("min_context", [])) or "요청"
        reasons.append(f"{who}의 min_context {req.min_context} 때문에 불가")
    return Candidate(
        id="c3",
        engine="sglang",
        model_id=m.model_id,
        args={"kv_cache_dtype": kv, "max_model_len": length} | s_args,
        predicted={"weight_gib": m.weight_bytes / 2**30, "kv_tokens_fit": float(fit)},
        reasons=reasons,
        blockers=blockers,
        requires_accuracy=(kv == "fp8"),
    )
