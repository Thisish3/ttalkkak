from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ttalkkak.errors import TtalkkakError
from ttalkkak.export import write_export
from ttalkkak.skills.loader import load_skill
from ttalkkak.ui.models import search_models
from ttalkkak.ui.runs import Busy, RunManager


@dataclass
class UiDeps:
    list_models: Callable
    detect_gpu: Callable
    run_fn: Callable[[str, list[str], str], None]


def _err(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def create_app(root: Path, deps: UiDeps) -> Starlette:
    runs = RunManager(root / "runs", deps.run_fn)
    page = (
        files("ttalkkak.ui").joinpath("static/index.html").read_text(encoding="utf-8")
    )

    async def index(request: Request):
        return HTMLResponse(page)

    async def gpu(request: Request):
        g = deps.detect_gpu()
        return JSONResponse(g.model_dump() if g else None)

    async def models(request: Request):
        q = request.query_params.get("q", "").strip()
        limit = min(int(request.query_params.get("limit", 20)), 50)
        try:
            rows = search_models(q, limit, deps.detect_gpu(), deps.list_models)
        except Exception as err:  # noqa: BLE001 - surface HF errors to the page
            return _err(f"HF 검색 실패: {err}", 502)
        return JSONResponse(rows)

    async def skills(request: Request):
        out = []
        sdir = root / "skills"
        for d in (
            sorted(p for p in sdir.iterdir() if p.is_dir()) if sdir.is_dir() else []
        ):
            try:
                s = load_skill(d)
            except TtalkkakError:
                continue
            out.append(
                {
                    "name": s.name,
                    "description": s.description,
                    "requires": s.ext.requires,
                    "min_context": s.ext.min_context,
                }
            )
        return JSONResponse(out)

    async def start(request: Request):
        body = await request.json()
        model = str(body.get("model") or "").strip()
        if not model:
            return _err("model is required", 400)
        try:
            run_id = runs.start(model, [str(s) for s in body.get("skills") or []])
        except Busy as busy:
            return _err(f"이미 실행 중입니다: {busy}", 409)
        return JSONResponse({"run_id": run_id})

    async def list_runs(request: Request):
        return JSONResponse(runs.list())

    async def status(request: Request):
        try:
            return JSONResponse(runs.status(request.path_params["run_id"]))
        except KeyError:
            return _err("run not found", 404)

    async def export(request: Request):
        run_id = request.path_params["run_id"]
        try:
            run_dir = runs.safe_dir(run_id)
        except KeyError:
            return _err("run not found", 404)
        body = await request.json()
        target = str(body.get("target") or "")
        try:
            written = write_export(
                run_dir,
                target,
                run_dir / f"export-{target}",
                force=True,
                image=body.get("image") or None,
            )
        except TtalkkakError as err:
            return _err(str(err), 400)
        return JSONResponse(
            {
                "files": [p.name for p in written],
                "dir": str(run_dir / f"export-{target}"),
            }
        )

    return Starlette(routes=[
        Route("/", index),
        Route("/api/gpu", gpu),
        Route("/api/models", models),
        Route("/api/skills", skills),
        Route("/api/runs", start, methods=["POST"]),
        Route("/api/runs", list_runs, methods=["GET"]),
        Route("/api/runs/{run_id}", status),
        Route("/api/runs/{run_id}/export", export, methods=["POST"]),
    ])  # fmt: skip
