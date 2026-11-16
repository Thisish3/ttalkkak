"""Operator token for remote console access."""

import hmac
import ipaddress
import secrets
import subprocess
from collections.abc import Callable
from pathlib import Path

from ttalkkak.client.config import write_private
from ttalkkak.errors import TtalkkakError

TOKEN_PREFIX = "ttk_op_"
TOKEN_FILE = "console-token"
UNAUTHORIZED = "운영자 토큰이 필요합니다"
TAURI_ORIGINS = frozenset(
    {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def save_token(path: Path, token: str) -> None:
    write_private(path, token + "\n")


def load_token(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def check(header: str | None, token: str) -> bool:
    """True when `header` is `Bearer <token>`; compared in constant time."""
    scheme, _, given = (header or "").partition(" ")
    ok = scheme.lower() == "bearer"
    return hmac.compare_digest(given.strip().encode(), token.encode()) and ok


def tailscale_ipv4() -> str | None:
    """First line of `tailscale ip -4`; None if tailscale is missing or fails."""
    try:
        r = subprocess.run(
            ["tailscale", "ip", "-4"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.strip().splitlines() if r.returncode == 0 else []
    try:
        return str(ipaddress.IPv4Address(lines[0].strip())) if lines else None
    except ValueError:
        return None


def remote_host(
    host: str | None, lookup: Callable[[], str | None] = tailscale_ipv4
) -> str:
    """Bind address for --remote: explicit --host, else the tailnet IPv4."""
    if host:
        return host
    ip = lookup()
    if ip is None:
        raise TtalkkakError(
            "tailscale 주소를 찾지 못했습니다 (`tailscale ip -4` 실패). "
            "tailscale을 켜거나 --host로 열 주소를 직접 지정하세요",
            2,
        )
    return ip
