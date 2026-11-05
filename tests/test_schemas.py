from ttalkkak.schemas import Candidate, Requirements, RunState


def test_candidate_roundtrip_keeps_blockers():
    c = Candidate(id="c1", engine="vllm", model_id="m", args={"max_model_len": 8192},
                  predicted={"kv_gib": 1.2}, reasons=["fits"], blockers=["x"])
    assert Candidate.model_validate_json(c.model_dump_json()) == c


def test_requirements_default_is_empty():
    r = Requirements()
    assert r.flags == {} and r.min_context == 0 and r.sources == {}


def test_runstate_tracks_done_stages():
    s = RunState(run_id="r1", done=["probe"])
    assert "probe" in s.done
