"""`ttalkkak` with no arguments: a minimal streaming chat (stage 6 replaces it)."""

import json
import sys
from collections.abc import Callable
from typing import TextIO

import httpx

from ttalkkak.client.config import ClientConfig, load_config
from ttalkkak.errors import TtalkkakError


def _login() -> int:
    import typer

    from ttalkkak.cli import login

    try:
        login(endpoint=None, issuer=None)
    except typer.Exit as e:
        return e.exit_code
    return 0


def _tokens(cfg: ClientConfig, http: httpx.Client, force: bool = False) -> str:
    from ttalkkak.auth.device import (
        DeviceLogin,
        load_tokens,
        save_tokens,
        valid_access_token,
    )

    if not force:
        return valid_access_token(cfg, http)
    t = load_tokens()
    if t is None or not cfg.issuer:
        raise TtalkkakError("로그인이 필요합니다: ttalkkak login", 2)
    t = DeviceLogin(cfg.issuer, cfg.client_id, http).refresh(t)
    save_tokens(t)
    return t.access_token


def _estimate(usage: dict, pricing: dict | None) -> str:
    prompt = usage.get("prompt_tokens", 0)
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    output = usage.get("completion_tokens", 0)
    line = f"입력 {prompt} (캐시 {cached}) · 출력 {output}"
    if pricing:
        krw = (
            (prompt - cached) * pricing["input"]
            + cached * pricing["cached_input"]
            + output * pricing["output"]
        ) / pricing.get("per", 1_000_000)
        line += f" · 약 {krw:.2f}원"
    return line


def _stream(http, url, token, body, out: TextIO) -> tuple[int, str, dict | None]:
    text, usage = [], None
    with http.stream(
        "POST", url, json=body, headers={"authorization": f"Bearer {token}"}
    ) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, "", None
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                text.append(piece)
                out.write(piece)
                out.flush()
            usage = chunk.get("usage") or usage
    return 200, "".join(text), usage


def main(
    http: httpx.Client | None = None,
    input_fn: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
    interactive: bool | None = None,
    login_fn: Callable[[], int] = _login,
) -> int:
    cfg = load_config()
    if cfg is None:
        if not (sys.stdin.isatty() if interactive is None else interactive):
            print("아직 접속 설정이 없습니다: ttalkkak login", file=sys.stderr)
            return 2
        print("처음 실행입니다. 로그인을 시작합니다.", file=out)
        code = login_fn()
        cfg = load_config()
        if code or cfg is None:
            return code
    http = http or httpx.Client(timeout=httpx.Timeout(30, read=300))
    base = cfg.endpoint.rstrip("/")
    try:
        token = _tokens(cfg, http)
        model = cfg.model
        if not model:
            r = http.get(
                f"{base}/v1/models", headers={"authorization": f"Bearer {token}"}
            )
            models = (
                [m["id"] for m in r.json().get("data", [])]
                if r.status_code == 200
                else []
            )
            if not models:
                raise TtalkkakError("사용할 수 있는 모델이 없습니다")
            model = models[0]
    except (TtalkkakError, httpx.HTTPError, ValueError) as err:
        print(f"오류: {err}", file=out)
        return getattr(err, "exit_code", 1)
    pricing = None
    try:
        r = http.get(f"{base}/v1/pricing")
        pricing = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        pass
    print(f"모델 {model} · 끝내려면 /exit", file=out)
    messages: list[dict] = []
    while True:
        try:
            text = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=out)
            return 0
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return 0
        messages.append({"role": "user", "content": text})
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            status, reply, usage = _stream(
                http, f"{base}/v1/chat/completions", token, body, out
            )
            if status == 401:
                token = _tokens(cfg, http, force=True)
                status, reply, usage = _stream(
                    http, f"{base}/v1/chat/completions", token, body, out
                )
            if status == 401:
                raise TtalkkakError("다시 로그인하세요: ttalkkak login", 2)
        except TtalkkakError as err:
            print(f"\n오류: {err}", file=out)
            return err.exit_code
        except (httpx.HTTPError, ValueError) as err:
            print(f"\n오류: 게이트웨이와 통신하지 못했습니다: {err}", file=out)
            messages.pop()
            continue
        if status != 200:
            print(f"\n오류: HTTP {status}", file=out)
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": reply})
        print(file=out)
        if usage:
            print(_estimate(usage, pricing), file=out)
