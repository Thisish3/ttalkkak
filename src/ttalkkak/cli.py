import time
import webbrowser

import httpx
import typer

from ttalkkak.errors import TtalkkakError
from ttalkkak.ops_cli import ops

app = typer.Typer(invoke_without_command=True, add_completion=False)
app.add_typer(ops, name="ops")

MOVED = ("up", "chat", "export", "ui")


@app.callback()
def main(ctx: typer.Context) -> None:
    """ttalkkak: 오픈소스 모델 기반 콘솔 에이전트 (운영자는 `ttalkkak ops`)."""
    if ctx.invoked_subcommand is None:
        from ttalkkak.client.console import main as console_main

        raise typer.Exit(console_main())


def _alias(name: str):
    def run(ctx: typer.Context) -> None:
        from typer.main import get_command

        typer.echo(
            f"알림: `ttalkkak {name}`은 `ttalkkak ops {name}`으로 바뀌었습니다",
            err=True,
        )
        get_command(ops).main(args=[name, *ctx.args], prog_name="ttalkkak ops")

    return run


for _name in MOVED:
    app.command(
        _name,
        hidden=True,
        context_settings={
            "allow_extra_args": True,
            "ignore_unknown_options": True,
            "help_option_names": [],
        },
    )(_alias(_name))


def _http() -> httpx.Client:
    return httpx.Client(timeout=30)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass


def _fail(err: TtalkkakError):
    typer.echo(f"오류: {err}", err=True)
    return typer.Exit(err.exit_code)


@app.command()
def login(
    endpoint: str | None = typer.Option(None, help="게이트웨이 주소"),
    issuer: str | None = typer.Option(None, help="로그인 서버(OIDC issuer)"),
) -> None:
    """브라우저로 로그인합니다."""
    from ttalkkak.auth.device import DeviceLogin, save_tokens, tenant_of
    from ttalkkak.client.config import ClientConfig, load_config, save_config

    old = load_config()
    endpoint = endpoint or (old.endpoint if old else None)
    endpoint = (endpoint or typer.prompt("게이트웨이 주소")).rstrip("/")
    client_id = old.client_id if old and old.endpoint == endpoint else "ttalkkak-cli"
    issuer = issuer or (old.issuer if old and old.endpoint == endpoint else None)
    http = _http()
    try:
        if not issuer:
            r = http.get(f"{endpoint}/v1/auth-config")
            if r.status_code != 200:
                raise TtalkkakError(
                    "게이트웨이에서 로그인 설정을 가져오지 못했습니다", 2
                )
            issuer, client_id = r.json()["issuer"], r.json().get("client_id", client_id)
        d = DeviceLogin(issuer, client_id, http)
        start = d.start()
        url = start.get("verification_uri_complete") or start["verification_uri"]
        typer.echo(f"브라우저에서 로그인하세요: {url}")
        typer.echo(f"코드: {start['user_code']}")
        _open_browser(url)
        tokens = d.poll(start)
        tenant = tenant_of(tokens.id_claims)
    except (TtalkkakError, httpx.HTTPError) as err:
        if isinstance(err, httpx.HTTPError):
            err = TtalkkakError(f"로그인 서버에 연결하지 못했습니다: {err}")
        raise _fail(err) from err
    save_config(
        ClientConfig(
            endpoint=endpoint,
            issuer=issuer,
            client_id=client_id,
            model=old.model if old and old.endpoint == endpoint else None,
        )
    )
    save_tokens(tokens)
    who = tokens.id_claims.get("email") or tokens.id_claims.get("sub")
    typer.echo(f"로그인했습니다: {who} (테넌트 {tenant})")


@app.command()
def logout() -> None:
    """로그아웃하고 저장된 토큰을 지웁니다."""
    from ttalkkak.auth.device import DeviceLogin, clear_tokens, load_tokens
    from ttalkkak.client.config import load_config

    cfg, t = load_config(), load_tokens()
    if t and cfg and cfg.issuer:
        try:
            DeviceLogin(cfg.issuer, cfg.client_id, _http()).revoke(t)
        except (TtalkkakError, httpx.HTTPError):
            typer.echo("알림: 서버에서 토큰을 폐기하지 못했습니다", err=True)
    clear_tokens()
    typer.echo("로그아웃했습니다")


@app.command()
def whoami() -> None:
    """로그인한 계정과 테넌트를 보여 줍니다."""
    from ttalkkak.auth.device import load_tokens, tenant_of

    t = load_tokens()
    if t is None:
        raise _fail(TtalkkakError("로그인이 필요합니다: ttalkkak login", 2))
    try:
        tenant = tenant_of(t.id_claims)
    except TtalkkakError as err:
        raise _fail(err) from err
    left = max(0, int(t.expires_at - time.time()))
    who = t.id_claims.get("email") or t.id_claims.get("sub")
    typer.echo(f"{who} · 테넌트 {tenant} · 접근 토큰 {left // 60}분 남음")
