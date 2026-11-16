import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ttalkkak.errors import TtalkkakError
from ttalkkak.export import write_export
from ttalkkak.skills.loader import load_skill
from ttalkkak.ui import auth
from ttalkkak.ui.models import search_models
from ttalkkak.ui.ops_routes import ops_routes
from ttalkkak.ui.runs import Busy, RunManager

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


@dataclass
class UiDeps:
    list_models: Callable
    detect_gpu: Callable
    run_fn: Callable[[str, list[str], str], None]
    store: object | None = None
    cluster_status: Callable[[], list[dict]] | None = None
    gateway_logs: Callable[[], list[str]] | None = None
    prometheus: object | None = None
    kubectl: Callable | None = None
    chip: object | None = None
    cost_config: object | None = None
    namespace: str = "ttalkkak"
    gateway_url: str = ""


def allowed_hosts(extra: Iterable[str] = ()) -> frozenset[str]:
    return LOCAL_HOSTS | frozenset(h.strip().lower() for h in extra if h.strip())


def _err(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


class _BadRequest(Exception):
    pass


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as err:
        raise _BadRequest("body must be JSON") from err
    if not isinstance(body, dict):
        raise _BadRequest("body must be a JSON object")
    return body


class _LocalOnly:
    """Reject foreign Host headers (DNS rebinding) and cross-site or non-JSON POSTs."""

    def __init__(self, app, allowed: frozenset[str]):
        self.app, self.allowed = app, allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = Request(scope)
        host = (req.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        resp = None
        if host not in self.allowed:
            resp = _err("forbidden host", 403)
        elif req.method == "POST":
            origin = req.headers.get("origin")
            if origin and urlsplit(origin).hostname not in self.allowed:
                resp = _err("cross-origin request refused", 403)
            elif not req.headers.get("content-type", "").startswith("application/json"):
                resp = _err("content-type must be application/json", 415)
        if resp is not None:
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


class _RemoteToken:
    """Operator-token boundary for remote mode: Bearer on /api/*, tauri CORS only."""

    def __init__(self, app, token_path: Path):
        self.app, self.token_path = app, token_path

    def _cors(self, origin: str | None) -> list[tuple[bytes, bytes]]:
        vary = (b"vary", b"Origin")
        if origin not in auth.TAURI_ORIGINS:
            return [vary]
        return [(b"access-control-allow-origin", origin.encode()), vary]

    def _authorized(self, headers) -> bool:
        token = auth.load_token(self.token_path)
        return token is not None and auth.check(headers.get("authorization"), token)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            if self._authorized(Headers(scope=scope)):
                return await self.app(scope, receive, send)
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = Request(scope)
        cors = self._cors(req.headers.get("origin"))
        if req.method == "OPTIONS":
            if len(cors) == 1:
                resp = _err("허용되지 않은 출처입니다", 403)
            else:
                resp = Response(status_code=204)
                resp.headers["access-control-allow-methods"] = "GET, POST, OPTIONS"
                resp.headers["access-control-allow-headers"] = (
                    "Authorization, Content-Type"
                )
                resp.headers["access-control-max-age"] = "600"
            resp.raw_headers.extend(cors)
            return await resp(scope, receive, send)
        if req.url.path.startswith("/api/") and not self._authorized(req.headers):
            resp = _err(auth.UNAUTHORIZED, 401)
            resp.raw_headers.extend(cors)
            return await resp(scope, receive, send)

        async def send_cors(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *cors]
            await send(message)

        return await self.app(scope, receive, send_cors)


def create_app(
    root: Path,
    deps: UiDeps,
    allowed_hosts: frozenset[str] = LOCAL_HOSTS,
    token_path: Path | None = None,
) -> Starlette:
    """With `token_path` the app is remote: the token replaces the Host check."""
    runs = RunManager(root / "runs", deps.run_fn)
    page = (
        files("ttalkkak.ui").joinpath("static/index.html").read_text(encoding="utf-8")
    )

    def index(request: Request):
        return HTMLResponse(page)

    def asset(name: str, media_type: str):
        body = files("ttalkkak.ui").joinpath(f"static/{name}").read_bytes()

        def handler(request: Request):
            return Response(body, media_type=media_type)

        return handler

    def gpu(request: Request):
        g = deps.detect_gpu()
        return JSONResponse(g.model_dump() if g else None)

    def models(request: Request):
        q = request.query_params.get("q", "").strip()
        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 50))
        except ValueError:
            return _err("limit must be an integer", 400)
        try:
            rows = search_models(q, limit, deps.detect_gpu(), deps.list_models)
        except Exception as err:  # noqa: BLE001 - surface HF errors to the page
            return _err(f"HF 검색 실패: {err}", 502)
        return JSONResponse(rows)

    def skills(request: Request):
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

    def list_runs(request: Request):
        return JSONResponse(runs.list())

    def status(request: Request):
        try:
            return JSONResponse(runs.status(request.path_params["run_id"]))
        except KeyError:
            return _err("run not found", 404)

    async def start(request: Request):
        try:
            body = await _body(request)
        except _BadRequest as err:
            return _err(str(err), 400)
        model = str(body.get("model") or "").strip()
        if not model:
            return _err("model is required", 400)
        if (
            not model.startswith("nim:")
            and await run_in_threadpool(deps.detect_gpu) is None
        ):
            return _err(
                "GPU를 찾지 못해 로컬 모델은 실행할 수 없습니다 (nim: 모델은 가능)", 400
            )
        try:
            run_id = runs.start(model, [str(s) for s in body.get("skills") or []])
        except Busy as busy:
            return _err(f"이미 실행 중입니다: {busy}", 409)
        return JSONResponse({"run_id": run_id})

    async def export(request: Request):
        try:
            body = await _body(request)
            run_dir = runs.safe_dir(request.path_params["run_id"])
        except _BadRequest as err:
            return _err(str(err), 400)
        except KeyError:
            return _err("run not found", 404)
        target = str(body.get("target") or "")
        out = run_dir / f"export-{target}"
        try:
            written = await run_in_threadpool(
                write_export, run_dir, target, out, True, body.get("image") or None
            )
        except TtalkkakError as err:
            return _err(str(err), 400)
        return JSONResponse({"files": [p.name for p in written], "dir": str(out)})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(runs.shutdown)

    app = Starlette(routes=[
        Route("/", index),
        Route("/app.css", asset("app.css", "text/css")),
        Route("/app.js", asset("app.js", "text/javascript")),
        Route("/connect.html", asset("connect.html", "text/html")),
        Route("/connect.js", asset("connect.js", "text/javascript")),
        Route("/api/gpu", gpu),
        Route("/api/models", models),
        Route("/api/skills", skills),
        Route("/api/runs", start, methods=["POST"]),
        Route("/api/runs", list_runs, methods=["GET"]),
        Route("/api/runs/{run_id}", status),
        Route("/api/runs/{run_id}/export", export, methods=["POST"]),
        *ops_routes(root, deps, _body, _BadRequest),
    ], lifespan=lifespan)  # fmt: skip
    if token_path is None:
        app.add_middleware(_LocalOnly, allowed=allowed_hosts)
    else:
        app.add_middleware(_RemoteToken, token_path=token_path)
    SYNC_HANDLERS.update(
        gpu=gpu, models=models, skills=skills, status=status, list_runs=list_runs
    )
    return app


# sync handlers run in Starlette's threadpool, so HF and subprocess calls
# never block the event loop (filled by create_app; inspected by tests)
SYNC_HANDLERS: dict[str, Callable] = {}
