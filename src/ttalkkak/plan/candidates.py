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
        raise TtalkkakError(
            "로컬 서빙 계획에는 HF 모델 정보와 NVIDIA GPU가 필요합니다", 2
        )
    parser_args, parser_blockers = _parser_args(m.family, req)
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
                args={"kv_cache_dtype": kv, "max_model_len": length,
                      "gpu_memory_utilization": 0.9} | parser_args,
                predicted={"weight_gib": m.weight_bytes / 2**30,
                           "kv_tokens_fit": float(fit)},
                reasons=reasons,
                blockers=blockers,
                requires_accuracy=(kv == "fp8"),
            )
        )
    return Plan(candidates=out, requirements=req)
