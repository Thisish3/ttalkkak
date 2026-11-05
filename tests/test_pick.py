from ttalkkak.pick import pick
from ttalkkak.schemas import (
    AppliedConfig,
    AppliedValue,
    Candidate,
    CandidateVerdict,
    CheckResult,
)
from ttalkkak.verify.applied import compare_applied


def test_compare_reports_mismatch_and_unknown():
    c = Candidate(
        id="c1",
        engine="vllm",
        model_id="m",
        args={
            "kv_cache_dtype": "fp8",
            "max_model_len": 16384,
            "tool_call_parser": "hermes",
        },
    )
    a = AppliedConfig(
        values={
            "kv_cache_dtype": AppliedValue(value="auto", evidence="metric"),
            "max_model_len": AppliedValue(value=16384, evidence="log"),
            "tool_call_parser": AppliedValue(value=None, evidence="unknown"),
        }
    )
    out = compare_applied(c, a)
    assert "kv_cache_dtype: 요청 fp8, 실제 auto (metric)" in out
    assert "tool_call_parser: 확인 불가" in out
    assert not any(s.startswith("max_model_len") for s in out)


def v(cid, tok_s, acc=None, agent_ok=True):
    return CandidateVerdict(
        candidate_id=cid,
        accuracy=acc,
        perf={"output_tok_s": tok_s},
        agent=[CheckResult(name="x", passed=agent_ok)],
    )


def test_fast_but_inaccurate_candidate_is_rejected():
    p = pick(
        [v("c1", 30, 0.62), v("c2", 45, 0.40)],
        baseline=0.62,
        launched_urls={"c1": "u1", "c2": "u2"},
    )
    assert p.chosen == "c1" and "정확도" in p.rejected["c2"] and p.base_url == "u1"


def test_agent_failure_rejects():
    p = pick(
        [v("c1", 30, agent_ok=False), v("c2", 20)],
        baseline=None,
        launched_urls={"c1": "u1", "c2": "u2"},
    )
    assert p.chosen == "c2" and "agent" in p.rejected["c1"]


def test_no_survivor():
    p = pick([v("c1", 30, agent_ok=False)], baseline=None, launched_urls={"c1": "u1"})
    assert p.chosen is None


def test_tie_prefers_earlier_candidate():
    p = pick(
        [v("c1", 30), v("c2", 30)],
        baseline=None,
        launched_urls={"c1": "u1", "c2": "u2"},
    )
    assert p.chosen == "c1"
