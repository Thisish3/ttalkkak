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
) -> None:
    deps = Deps(
        probe=probe,
        check_docker=check_docker,
        launch=lambda c, a, p: launch(c, a, platform=p),
        stop=stop,
        client=httpx.Client(),
        run_accuracy=run_accuracy,
        run_perf=run_perf,
    )
    try:
        run_dir, chosen = run_up(
            model, _names(skills), ROOT / "runs", ROOT / "skills", deps, resume
        )
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    report = run_dir / "report.md"
    typer.echo(
        report.read_text(encoding="utf-8") if report.exists() else f"run: {run_dir}"
    )
    raise typer.Exit(0 if chosen.chosen else 1)


@app.command()
def chat(run: str = typer.Option(...), skills: str = typer.Option("")) -> None:
    import asyncio

    from ttalkkak.agent.runtime import build_agent
    from ttalkkak.pipeline import resolve_endpoint
    from ttalkkak.skills.loader import load_skills

    try:
        base_url, model, key = resolve_endpoint(ROOT / "runs" / run)
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    sk = load_skills(ROOT / "skills", _names(skills))

    async def loop():
        agent, adapter = await build_agent(base_url, model, sk, api_key=key)
        msgs = []
        try:
            while (text := input("> ").strip()) not in ("", "exit"):
                msgs.append({"role": "user", "content": text})
                out = await agent.ainvoke({"messages": msgs})
                msgs = out["messages"]
                print(msgs[-1].content)
        finally:
            if adapter:
                await adapter.__aexit__(None, None, None)

    asyncio.run(loop())
