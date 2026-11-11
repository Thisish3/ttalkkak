import typer

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
