import json

import httpx

from ttalkkak.schemas import CheckResult, Requirements
from ttalkkak.verify.openai_client import ChatResult, chat

WEATHER_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]
ASK_WEATHER = [
    {"role": "user", "content": "What is the weather in Seoul? Use the tool."}
]
ASK_TWO = [{"role": "user", "content": "Get the weather for Seoul and for Busan."}]
ASK_THINK = [{"role": "user", "content": "Is 91 prime? Answer yes or no."}]
ANSWER_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
}


def _valid_json_answer(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("answer"), str)


def _valid_call(r: ChatResult) -> bool:
    if not r.tool_calls or r.tool_calls[0].name != "get_weather":
        return False
    try:
        return "city" in json.loads(r.tool_calls[0].arguments)
    except json.JSONDecodeError:
        return False


def run_agent_checks(
    client: httpx.Client,
    base_url: str,
    model: str,
    req: Requirements,
    api_key: str | None = None,
) -> list[CheckResult]:
    f, out = req.flags, []
    tools = WEATHER_TOOL if f.get("tool_calling") else None
    if f.get("tool_calling"):
        r = chat(client, base_url, model, ASK_WEATHER, tools=tools, api_key=api_key)
        out.append(
            CheckResult(
                name="tool_call_parsed",
                passed=_valid_call(r),
                detail=str([t.model_dump() for t in r.tool_calls]),
            )
        )
    if f.get("reasoning"):
        r = chat(client, base_url, model, ASK_THINK, api_key=api_key)
        leaked = "<think>" in r.content or "</think>" in r.content
        out.append(
            CheckResult(
                name="reasoning_not_in_content",
                passed=not leaked,
                detail=r.content[:200],
            )
        )
    if f.get("streaming"):
        msgs = ASK_WEATHER if tools else ASK_THINK
        a = chat(client, base_url, model, msgs, tools=tools, api_key=api_key)
        b = chat(
            client, base_url, model, msgs, tools=tools, stream=True, api_key=api_key
        )
        same = a.content.strip() == b.content.strip() and [
            t.name for t in a.tool_calls
        ] == [t.name for t in b.tool_calls]
        out.append(
            CheckResult(
                name="stream_matches_nonstream",
                passed=same,
                detail=f"nonstream={a.model_dump()} stream={b.model_dump()}",
            )
        )
    if f.get("single_tool_call"):
        r = chat(
            client,
            base_url,
            model,
            ASK_TWO,
            tools=WEATHER_TOOL,
            extra={"parallel_tool_calls": False},
            api_key=api_key,
        )
        out.append(
            CheckResult(
                name="single_tool_call",
                passed=len(r.tool_calls) <= 1,
                detail=f"{len(r.tool_calls)} calls",
            )
        )
    if f.get("structured_output"):
        r = chat(
            client,
            base_url,
            model,
            ASK_THINK,
            extra={"response_format": ANSWER_SCHEMA},
            api_key=api_key,
        )
        out.append(
            CheckResult(
                name="structured_output_valid",
                passed=_valid_json_answer(r.content),
                detail=r.content[:200],
            )
        )
    return out
