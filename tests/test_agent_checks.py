import httpx
from mock_openai import mock_transport

from ttalkkak.schemas import Requirements
from ttalkkak.verify.agent import run_agent_checks
from ttalkkak.verify.openai_client import chat

ALL = Requirements(
    flags={
        "tool_calling": True,
        "reasoning": True,
        "streaming": True,
        "single_tool_call": True,
    }
)


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


def test_stream_assembles_split_arguments_and_ignores_usage_chunk():
    c = httpx.Client(transport=mock_transport("split_args"))
    r = chat(
        c,
        "http://m/v1",
        "m",
        [{"role": "user", "content": "x"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "get_weather", "parameters": {"type": "object"}},
            }
        ],
        stream=True,
    )
    assert [(t.name, t.arguments) for t in r.tool_calls] == [
        ("get_weather", '{"city": "Seoul"}')
    ]


def test_only_requested_checks_run():
    names = results("good", Requirements(flags={"streaming": True}))
    assert set(names) == {"stream_matches_nonstream"}


SO = Requirements(flags={"structured_output": True})


def test_structured_output_passes_on_valid_json():
    assert results("good", SO) == {"structured_output_valid": True}


def test_structured_output_fails_on_plain_text():
    assert results("bad_json", SO) == {"structured_output_valid": False}
