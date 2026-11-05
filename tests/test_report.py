from ttalkkak.report import render_report
from ttalkkak.schemas import (
    AppliedConfig,
    AppliedValue,
    Candidate,
    CandidateVerdict,
    Launched,
    Pick,
    Plan,
    Requirements,
    Verification,
)


def test_report_has_warning_table_and_failure():
    plan = Plan(
        requirements=Requirements(
            flags={"tool_calling": True}, sources={"tool_calling": ["web-fetch"]}
        ),
        candidates=[
            Candidate(
                id="c1",
                engine="vllm",
                model_id="m",
                args={"max_model_len": 8192},
                predicted={"kv_tokens_fit": 9000},
            ),
            Candidate(id="c2", engine="vllm", model_id="m", args={}),
        ],
    )
    launched = [
        Launched(candidate_id="c1", status="ready", base_url="http://h:8000"),
        Launched(
            candidate_id="c2",
            status="failed",
            failure="oom",
            log_tail=["CUDA out of memory"],
        ),
    ]
    ver = Verification(
        verdicts=[
            CandidateVerdict(
                candidate_id="c1",
                mismatches=["kv_cache_dtype: 요청 fp8, 실제 auto (metric)"],
                applied=AppliedConfig(
                    values={"kv_cache_tokens": AppliedValue(value=7000, evidence="log")}
                ),
                perf={"output_tok_s": 30.0, "ttft_ms": 120.0},
            )
        ]
    )
    md = render_report(plan, launched, ver, Pick(chosen="c1", base_url="http://h:8000"))
    assert md.index("경고") < md.index("예측 vs 실측")
    assert "kv_cache_dtype: 요청 fp8, 실제 auto" in md
    assert "| c1 | 8192 |" in md and "9000 / 7000" in md
    assert "oom" in md and "CUDA out of memory" in md
    assert "tool_calling ← web-fetch" in md
    assert 'base_url="http://h:8000/v1"' in md


def test_report_shows_reasons_of_blocked_candidates():
    plan = Plan(
        requirements=Requirements(),
        candidates=[
            Candidate(
                id="c1",
                engine="vllm",
                model_id="m",
                args={},
                blockers=["max_model_len 100 < 필요 32768"],
                reasons=["file-qa의 min_context 32768 때문에 불가"],
            )
        ],
    )
    md = render_report(plan, [], Verification(verdicts=[]), Pick(chosen=None))
    assert "file-qa의 min_context 32768 때문에 불가" in md
