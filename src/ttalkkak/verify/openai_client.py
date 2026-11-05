import json

import httpx
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    arguments: str


class ChatResult(BaseModel):
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def chat(
    client: httpx.Client,
    base_url: str,
    model: str,
    messages: list[dict],
    *,
    tools=None,
    stream=False,
    extra: dict | None = None,
    api_key: str | None = None,
) -> ChatResult:
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 256,
        "stream": stream,
    } | (extra or {})
    if tools:
        body["tools"] = tools
    url = f"{base_url}/chat/completions"
    if not stream:
        resp = client.post(url, json=body, headers=_headers(api_key), timeout=120)
        msg = resp.json()["choices"][0]["message"]
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
            tool_calls=[
                ToolCall(
                    name=t["function"]["name"],
                    arguments=t["function"].get("arguments") or "",
                )
                for t in msg.get("tool_calls") or []
            ],
        )
    content, reasoning, calls = [], [], {}
    with client.stream(
        "POST", url, json=body, headers=_headers(api_key), timeout=120
    ) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            for choice in json.loads(data).get("choices") or []:
                d = choice.get("delta") or {}
                content.append(d.get("content") or "")
                reasoning.append(d.get("reasoning_content") or d.get("reasoning") or "")
                for t in d.get("tool_calls") or []:
                    slot = calls.setdefault(t["index"], {"name": "", "arguments": ""})
                    fn = t.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
    return ChatResult(
        content="".join(content),
        reasoning="".join(reasoning),
        tool_calls=[ToolCall(**calls[i]) for i in sorted(calls)],
    )
