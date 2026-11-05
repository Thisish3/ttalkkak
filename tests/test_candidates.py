from ttalkkak.plan.candidates import make_plan
from ttalkkak.schemas import GpuInfo, ModelInfo, ProbeResult, Requirements

M = ModelInfo(
    model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
    family="qwen2",
    num_layers=28,
    num_kv_heads=4,
    head_dim=128,
    dtype="float16",
    quantization="awq",
    max_position=32768,
    weight_bytes=5_580_000_000,
)
G = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def req(**kw):
    return Requirements(**kw)


def test_two_candidates_auto_and_fp8():
    p = make_plan(ProbeResult(model=M, gpu=G), req())
    assert [c.args["kv_cache_dtype"] for c in p.candidates] == ["auto", "fp8"]
    assert p.candidates[1].requires_accuracy


def test_tool_calling_sets_parser():
    p = make_plan(
        ProbeResult(model=M, gpu=G),
        req(flags={"tool_calling": True}, sources={"tool_calling": ["web-fetch"]}),
    )
    c = p.candidates[0]
    assert c.args["enable_auto_tool_choice"] is True
    assert c.args["tool_call_parser"] == "hermes"


def test_unknown_family_blocks_when_tools_needed():
    m = M.model_copy(update={"family": "unknownlm"})
    p = make_plan(ProbeResult(model=m, gpu=G), req(flags={"tool_calling": True}))
    assert all("no tool parser known for unknownlm" in c.blockers for c in p.candidates)


def test_min_context_too_big_blocks_and_names_skill():
    p = make_plan(
        ProbeResult(model=M, gpu=G),
        req(min_context=10**6, sources={"min_context": ["file-qa"]}),
    )
    assert all(c.blockers for c in p.candidates)
    assert any("file-qa" in r for r in p.candidates[0].reasons)


def test_max_model_len_never_exceeds_model_limit():
    big_gpu = G.model_copy(update={"memory_mib": 192 * 1024})
    p = make_plan(ProbeResult(model=M, gpu=big_gpu), req())
    assert all(c.args["max_model_len"] == 32768 for c in p.candidates)
