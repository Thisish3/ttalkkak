from pathlib import Path

import httpx
import typer

from ttalkkak.errors import TtalkkakError

ops = typer.Typer(no_args_is_help=True, help="운영자용 명령: 서빙 실행, 내보내기, 화면")
ROOT = Path.cwd()


def _names(skills: str) -> list[str]:
    return [s for s in skills.split(",") if s]


@ops.command()
def up(
    model: str = typer.Option(...),
    skills: str = typer.Option(""),
    resume: str | None = None,
) -> None:
    Deps, run_up, deps_kw = _serving()
    deps = Deps(
        client=httpx.Client(),
        **deps_kw,
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


@ops.command()
def chat(run: str = typer.Option(...), skills: str = typer.Option("")) -> None:
    import asyncio

    _need_extra("langchain", "langgraph", "fastmcp")
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


@ops.command()
def export(
    run: str = typer.Option(...),
    target: str = typer.Option(..., help="k8s | kserve | llmd | dynamo"),
    out: str = typer.Option(...),
    image: str | None = typer.Option(None, help="override image (required for dynamo)"),
    force: bool = False,
) -> None:
    from ttalkkak.export import write_export

    try:
        written = write_export(ROOT / "runs" / run, target, Path(out), force, image)
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    for p in written:
        typer.echo(str(p))


@ops.command()
def ui(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Open the local web UI (model search, run, report, export)."""
    _need_extra("uvicorn", "huggingface_hub", "starlette")
    import uvicorn
    from huggingface_hub import HfApi

    from ttalkkak.probe.gpu import detect_gpu
    from ttalkkak.ui.app import UiDeps, create_app

    Deps, run_up, deps_kw = _serving()
    if host != "127.0.0.1":
        typer.echo(
            f"경고: {host}에 엽니다. 이 화면은 컨테이너를 띄우고 코드를 실행할 수 있으니 "
            "신뢰하는 네트워크에서만 쓰세요.",
            err=True,
        )

    def run_fn(model: str, skills: list[str], run_id: str) -> None:
        deps = Deps(
            client=httpx.Client(),
            **deps_kw,
        )
        run_up(model, skills, ROOT / "runs", ROOT / "skills", deps, run_id=run_id)

    deps = UiDeps(list_models=HfApi().list_models, detect_gpu=detect_gpu, run_fn=run_fn)
    typer.echo(f"ttalkkak ui: http://{host}:{port}")
    uvicorn.run(create_app(ROOT, deps), host=host, port=port, log_level="warning")


def _need_extra(*modules: str) -> None:
    import importlib.util

    if any(importlib.util.find_spec(m) is None for m in modules):
        typer.echo(
            '이 명령에는 ttalkkak[ops]가 필요합니다: uv tool install "ttalkkak[ops]"',
            err=True,
        )
        raise typer.Exit(2)


def _serving():
    from ttalkkak.launch.docker import check_docker, launch, stop
    from ttalkkak.pipeline import Deps, run_up
    from ttalkkak.probe import probe
    from ttalkkak.verify.accuracy import run_accuracy
    from ttalkkak.verify.perf import run_perf

    kw = {
        "probe": probe,
        "check_docker": check_docker,
        "launch": lambda c, a, p: launch(c, a, platform=p),
        "stop": stop,
        "run_accuracy": run_accuracy,
        "run_perf": run_perf,
    }
    return Deps, run_up, kw
