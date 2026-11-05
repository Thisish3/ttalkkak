from ttalkkak.schemas import AppliedConfig, Candidate

COMPARED = (
    "kv_cache_dtype",
    "max_model_len",
    "tool_call_parser",
    "reasoning_parser",
    "enable_auto_tool_choice",
)


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
