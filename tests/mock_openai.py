import json

import httpx

WEATHER = {"name": "get_weather", "arguments": '{"city": "Seoul"}'}


def _sse(chunks: list[dict]) -> str:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


def _delta(**d):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def mock_transport(behavior: str) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        stream, tools = body.get("stream"), body.get("tools")
        calls = [WEATHER] if tools else []
        if behavior == "two_calls" and tools:
            calls = [WEATHER, {"name": "get_weather", "arguments": '{"city": "Busan"}'}]
        content = "" if calls else "Hello"
        if behavior == "think_leak":
            content = "<think>hmm</think>Hello"
        if not stream:
            msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": c}
                    for i, c in enumerate(calls)
                ]
                or None,
            }
            return httpx.Response(200, json={"choices": [{"index": 0, "message": msg}]})
        if behavior == "stream_mismatch":
            content, calls = "Different", []
        chunks = [_delta(role="assistant")]
        if content:
            chunks.append(_delta(content=content))
        for i, c in enumerate(calls):
            if behavior == "split_args":
                a = c["arguments"]
                chunks.append(
                    _delta(
                        tool_calls=[
                            {
                                "index": i,
                                "id": f"c{i}",
                                "type": "function",
                                "function": {"name": c["name"], "arguments": a[:5]},
                            }
                        ]
                    )
                )
                chunks.append(
                    _delta(tool_calls=[{"index": i, "function": {"arguments": a[5:]}}])
                )
            else:
                chunks.append(
                    _delta(
                        tool_calls=[
                            {
                                "index": i,
                                "id": f"c{i}",
                                "type": "function",
                                "function": c,
                            }
                        ]
                    )
                )
        chunks.append({"choices": [], "usage": {"completion_tokens": 3}})
        return httpx.Response(
            200, text=_sse(chunks), headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)
