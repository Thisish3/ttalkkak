import io
import json
import time

import httpx
from fake_oidc import ISSUER, make_oidc

from ttalkkak.auth.device import Tokens, load_tokens, save_tokens
from ttalkkak.client.config import ClientConfig, save_config
from ttalkkak.client.console import main

GW = "https://gw.test"


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def delta(text):
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


USAGE = {
    "choices": [],
    "usage": {
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "prompt_tokens_details": {"cached_tokens": 64},
    },
}


def setup(tmp_path, monkeypatch, chat_handler, expires_in=900):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    save_config(ClientConfig(endpoint=GW, issuer=ISSUER))
    save_tokens(
        Tokens(
            "at-0", "rt-0", time.time() + expires_in, {"sub": "u1", "groups": ["acme"]}
        )
    )
    _, idp = make_oidc()
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "idp.test":
            return idp.handler(req)
        seen.append(req)
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen"}]})
        if req.url.path == "/v1/pricing":
            return httpx.Response(
                200,
                json={"input": 1000, "cached_input": 100, "output": 2000, "per": 1e6},
            )
        return chat_handler(req)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def run(http, inputs):
    it = iter(inputs)

    def input_fn(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    out = io.StringIO()
    code = main(http=http, input_fn=input_fn, out=out)
    return code, out.getvalue()


def test_streams_reply_and_prints_usage_and_estimate(tmp_path, monkeypatch):
    http, seen = setup(
        tmp_path, monkeypatch, lambda req: sse(delta("안녕"), delta("하세요"), USAGE)
    )
    code, out = run(http, ["hi", "/exit"])
    assert code == 0 and "안녕하세요" in out
    # (120-64)*1000 + 64*100 + 45*2000 = 152400 / 1e6 = 0.15
    assert "입력 120 (캐시 64) · 출력 45 · 약 0.15원" in out
    chat = next(r for r in seen if r.url.path == "/v1/chat/completions")
    body = json.loads(chat.content)
    assert body["model"] == "qwen" and body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert chat.headers["authorization"] == "Bearer at-0"


def test_history_is_sent_on_next_turn(tmp_path, monkeypatch):
    http, seen = setup(tmp_path, monkeypatch, lambda req: sse(delta("a"), USAGE))
    run(http, ["one", "two"])
    chats = [
        json.loads(r.content) for r in seen if r.url.path == "/v1/chat/completions"
    ]
    assert [m["role"] for m in chats[1]["messages"]] == ["user", "assistant", "user"]


def test_refreshes_once_on_401_then_asks_to_login(tmp_path, monkeypatch):
    calls = []

    def chat(req):
        calls.append(req.headers["authorization"])
        return httpx.Response(401)

    http, _ = setup(tmp_path, monkeypatch, chat)
    code, out = run(http, ["hi"])
    assert code == 2 and "ttalkkak login" in out
    assert len(calls) == 2 and calls[0] != calls[1]
    assert load_tokens().access_token != "at-0"


def test_expired_token_is_refreshed_before_first_request(tmp_path, monkeypatch):
    calls = []

    def chat(req):
        calls.append(req.headers["authorization"])
        return sse(delta("ok"), USAGE)

    http, _ = setup(tmp_path, monkeypatch, chat, expires_in=10)
    run(http, ["hi"])
    assert calls == ["Bearer at-1"]


def test_first_run_without_config_starts_login(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path)
    started = []
    out = io.StringIO()
    code = main(
        http=httpx.Client(),
        input_fn=lambda p: "",
        out=out,
        interactive=True,
        login_fn=lambda: started.append(1) or 0,
    )
    assert started == [1] and code == 0
