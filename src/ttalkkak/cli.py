from pathlib import Path

import httpx
import typer

from ttalkkak.errors import TtalkkakError
from ttalkkak.launch.docker import check_docker, launch, stop
from ttalkkak.pipeline import Deps, run_up
from ttalkkak.probe import probe
from ttalkkak.verify.accuracy import run_accuracy
from ttalkkak.verify.perf import run_perf

app = typer.Typer(no_args_is_help=True)
ROOT = Path.cwd()


@app.callback()
def main() -> None:
    """ttalkkak: agent-ready LLM serving."""


def _names(skills: str) -> list[str]:
    return [s for s in skills.split(",") if s]


@app.command()
def up(
    model: str = typer.Option(...),
    skills: str = typer.Option(""),
    resume: str | None = None,
    keep_all: bool = False,
) -> None:
    deps = Deps(
        probe=probe,
        check_docker=check_docker,
        launch=lambda c, a: launch(c, a),
        stop=stop,
        client=httpx.Client(),
        run_accuracy=run_accuracy,
        run_perf=run_perf,
    )
    try:
        run_dir, chosen = run_up(
            model,
            _names(skills),
            ROOT / "runs",
            ROOT / "skills",
            deps,
            resume,
            keep_all,
        )
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    report = run_dir / "report.md"
    typer.echo(
        report.read_text(encoding="utf-8") if report.exists() else f"run: {run_dir}"
    )
    raise typer.Exit(0 if chosen.chosen else 1)
