from pathlib import Path

import httpx
import typer

from ttalkkak.errors import TtalkkakError

ops = typer.Typer(no_args_is_help=True, help="운영자용 명령: 서빙 실행, 내보내기, 화면")
ROOT = Path.cwd()
TK_HOME = Path.home() / ".ttalkkak"
DB_OPT = typer.Option(None, "--db", help="기본 ~/.ttalkkak/tenants.db")


def _names(skills: str) -> list[str]:
    return [s for s in skills.split(",") if s]


@ops.command()
def up(
    model: str = typer.Option(...),
    skills: str = typer.Option(""),
    resume: str | None = None,
) -> None:
    _need_extra("huggingface_hub")
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
    target: str = typer.Option(..., help="k8s | kserve | llmd | dynamo | gateway"),
    out: str = typer.Option(...),
    image: str | None = typer.Option(None, help="override image (required for dynamo)"),
    force: bool = False,
    replicas: int = typer.Option(
        1, help="gateway: vLLM replicas (cost.json wins if 1)"
    ),
    issuer: str | None = typer.Option(None, help="gateway: external OIDC issuer"),
    hostname: str = typer.Option("ttalkkak.local", help="gateway: public hostname"),
    db: str | None = DB_OPT,
) -> None:
    from ttalkkak.export import write_export

    try:
        if target == "gateway":
            written = _export_gateway(
                ROOT / "runs" / run, Path(out), force, image, replicas, issuer,
                hostname, db,
            )  # fmt: skip
        else:
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


tenants = typer.Typer(no_args_is_help=True, help="테넌트와 API 키")
ops.add_typer(tenants, name="tenants")


def _store(db: str | None):
    from ttalkkak.tenants.store import TenantStore

    return TenantStore(Path(db) if db else TK_HOME / "tenants.db")


def _tenant_cmd(fn):
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TtalkkakError as err:
            typer.echo(f"오류: {err}", err=True)
            raise typer.Exit(err.exit_code) from err

    return run


@tenants.command("add")
@_tenant_cmd
def tenants_add(tid: str, name: str = typer.Option(...), db: str | None = DB_OPT):
    _store(db).add_tenant(tid, name)
    typer.echo(f"테넌트 {tid}를 추가했습니다")


@tenants.command("list")
@_tenant_cmd
def tenants_list(db: str | None = DB_OPT):
    st = _store(db)
    counts: dict[str, int] = {}
    for k in st.active_keys():
        counts[k.tenant_id] = counts.get(k.tenant_id, 0) + 1
    typer.echo(f"{'id':<16}{'이름':<16}{'분당 토큰':>10}  {'캐시':<10}{'활성 키':>6}")
    for t in st.list_tenants():
        limit = "-" if t.token_limit_per_min is None else str(t.token_limit_per_min)
        typer.echo(
            f"{t.id:<16}{t.name:<16}{limit:>10}  {t.cache_mode:<10}"
            f"{counts.get(t.id, 0):>6}"
        )


@tenants.command("key-issue")
@_tenant_cmd
def tenants_key_issue(tid: str, db: str | None = DB_OPT):
    typer.echo(_store(db).issue_key(tid))
    typer.echo("이 키는 다시 볼 수 없습니다. 지금 안전한 곳에 저장하세요.", err=True)


@tenants.command("key-rotate")
@_tenant_cmd
def tenants_key_rotate(tid: str, grace_hours: int = 24, db: str | None = DB_OPT):
    typer.echo(_store(db).rotate_key(tid, grace_hours))
    typer.echo(
        f"새 키입니다. 이전 키는 {grace_hours}시간 뒤 만료되고, 이 키는 다시 볼 수 없습니다.",
        err=True,
    )


@tenants.command("key-revoke")
@_tenant_cmd
def tenants_key_revoke(prefix: str, db: str | None = DB_OPT):
    _store(db).revoke_key(prefix)
    typer.echo(f"{prefix} 키를 폐기했습니다")


@tenants.command("set-limit")
@_tenant_cmd
def tenants_set_limit(tid: str, per_min: str, db: str | None = DB_OPT):
    if per_min != "none" and not per_min.isdigit():
        raise TtalkkakError("분당 토큰은 숫자 또는 none입니다", 2)
    _store(db).set_limit(tid, None if per_min == "none" else int(per_min))
    typer.echo(f"{tid} 분당 토큰 한도: {per_min}")


@tenants.command("set-cache")
@_tenant_cmd
def tenants_set_cache(tid: str, mode: str, db: str | None = DB_OPT):
    _store(db).set_cache(tid, mode)
    typer.echo(f"{tid} 캐시: {mode}")


@tenants.command("rotate-salt")
@_tenant_cmd
def tenants_rotate_salt(tid: str, db: str | None = DB_OPT):
    v = _store(db).rotate_salt(tid)
    typer.echo(f"{tid} 캐시 salt 버전 {v}: 이전 캐시는 더 이상 재사용되지 않습니다")


def _dex_users_path() -> Path:
    return TK_HOME / "dex-users.yaml"


def _load_dex_users() -> list[dict]:
    import yaml

    p = _dex_users_path()
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or []) if p.is_file() else []


def _save_dex_users(users: list[dict]) -> None:
    import yaml

    from ttalkkak.client.config import write_private

    write_private(_dex_users_path(), yaml.safe_dump(users, sort_keys=False))


@tenants.command("user-add")
@_tenant_cmd
def tenants_user_add(
    tid: str,
    email: str,
    password_stdin: bool = typer.Option(False, help="비밀번호를 표준입력에서 읽기"),
    db: str | None = DB_OPT,
):
    """로그인 서버(Dex)에 사용자를 추가합니다. 사용자는 테넌트 하나에만 속합니다."""
    import hashlib
    import sys

    import bcrypt

    _store(db)._tenant(tid)
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = typer.prompt("비밀번호", hide_input=True, confirmation_prompt=True)
    if len(password) < 8:
        raise TtalkkakError("비밀번호는 8자 이상이어야 합니다", 2)
    users = [u for u in _load_dex_users() if u["email"] != email]
    users.append(
        {
            "email": email,
            "username": email,
            "userID": hashlib.sha256(email.encode()).hexdigest()[:32],
            "hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            "groups": [tid],
        }
    )
    _save_dex_users(users)
    typer.echo(
        f"{email}을 테넌트 {tid}에 추가했습니다. `ops export --target gateway`로 반영하세요"
    )


@tenants.command("user-remove")
@_tenant_cmd
def tenants_user_remove(email: str, db: str | None = DB_OPT):
    users = _load_dex_users()
    left = [u for u in users if u["email"] != email]
    if len(left) == len(users):
        raise TtalkkakError(f"사용자 {email}이 없습니다", 2)
    _save_dex_users(left)
    typer.echo(f"{email}을 지웠습니다")


def _export_gateway(run_dir, out, force, image, replicas, issuer, hostname, db):
    from ttalkkak.cost.model import CostResult
    from ttalkkak.gateway.manifests import GatewayOptions, render_stack

    cost_path = run_dir / "cost.json"
    cost = (
        CostResult.model_validate_json(cost_path.read_text(encoding="utf-8"))
        if cost_path.is_file()
        else None
    )
    opt = GatewayOptions(
        replicas=replicas,
        issuer=issuer,
        hostname=hostname,
        image=image,
        dex_users=_load_dex_users(),
    )
    files = render_stack(run_dir, _store(db), cost, opt)
    existing = [out / n for n in files if (out / n).exists()]
    if existing and not force:
        raise TtalkkakError(f"{out}에 파일이 이미 있습니다 (--force로 덮어쓰기)", 2)
    from ttalkkak.client.config import write_private

    written = []
    for name, text in files.items():
        # 00-namespace.yaml and 15-dex.yaml carry key hashes and password hashes
        write_private(out / name, text)
        written.append(out / name)
    return written


@ops.command()
def bill(
    logs: str | None = typer.Option(None, help="접근 로그 파일 또는 *.jsonl 디렉터리"),
    from_cluster: bool = typer.Option(
        False, help="게이트웨이 파드 로그를 kubectl로 읽기"
    ),
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD (포함, UTC)"),
    to: str = typer.Option(..., help="YYYY-MM-DD (제외, UTC)"),
    cost: str = typer.Option(..., help="runs/<run>/cost.json"),
    discounts: str | None = typer.Option(None, help="tenant: 할인율(0~1) YAML"),
    out: str = ".",
    prune_logs: bool = typer.Option(False, "--prune", help="보관 기간 지난 로그 삭제"),
    retention_days: int = 120,
) -> None:
    """기간 사용량을 집계해 청구서 CSV와 CloudEvents를 만듭니다."""
    import json
    from datetime import UTC, datetime

    import yaml

    from ttalkkak.billing import bill as b
    from ttalkkak.cost.model import Prices

    try:
        start, end = (
            datetime.fromisoformat(d).replace(tzinfo=UTC) for d in (from_, to)
        )
        prices = Prices(**json.loads(Path(cost).read_text())["price_krw_per_mtok"])
        disc = yaml.safe_load(Path(discounts).read_text()) if discounts else {}
    except (ValueError, KeyError, OSError) as err:
        typer.echo(f"오류: 입력을 읽지 못했습니다: {err}", err=True)
        raise typer.Exit(2) from err
    if from_cluster == (logs is not None):
        typer.echo("오류: --logs 또는 --from-cluster 중 하나를 주세요", err=True)
        raise typer.Exit(2)
    if from_cluster:
        from ttalkkak.cluster.apply import gateway_logs

        try:
            cluster_lines = gateway_logs()
        except TtalkkakError as err:
            typer.echo(f"오류: {err}", err=True)
            raise typer.Exit(err.exit_code) from err
    src = Path(logs) if logs else Path(".")
    files = sorted(src.glob("*.jsonl")) if src.is_dir() else [src]

    def records(stats):
        if from_cluster:
            yield from b.read_log(cluster_lines, stats)
            return
        for f in files:
            with f.open(encoding="utf-8") as fh:
                yield from b.read_log(fh, stats)

    stats: dict = {}
    usage = b.aggregate(records(stats), start, end)
    tag = f"{from_}_{to}"
    inv = Path(out) / f"invoice-{tag}.csv"
    b.write_invoice(b.invoice_rows(usage, prices, disc or {}), inv)
    b.write_cloudevents(
        records({}), start, end, Path(out) / f"usage-{tag}.cloudevents.jsonl"
    )
    typer.echo(
        f"{inv} · 테넌트 {len({u.tenant for u in usage})} · 건너뛴 줄 {stats.get('skipped', 0)}"
    )
    if prune_logs and logs and src.is_dir():
        for p in b.prune(src, retention_days, datetime.now(UTC)):
            typer.echo(f"삭제: {p}")


@ops.command()
def serve(
    tenants_json: str | None = typer.Option(
        None, help="tenants.json (클러스터 Secret)"
    ),
    db: str | None = DB_OPT,
    cost: str | None = typer.Option(None, help="cost.json"),
    logs: str | None = typer.Option(None, help="접근 로그 파일/디렉터리"),
    logs_from_cluster: bool = typer.Option(False, help="Envoy 파드 로그를 API로 읽기"),
    issuer: str = typer.Option(..., help="OIDC issuer"),
    client_id: str = "ttalkkak-cli",
    jwks_url: str | None = typer.Option(None, help="기본 <issuer>/keys"),
    host: str = "127.0.0.1",
    port: int = 8790,
) -> None:
    """게이트웨이 인증(/authz), 가격표, 사용량, 테넌트 지표 서버."""
    _need_extra("uvicorn", "starlette", "jwt")
    import uvicorn

    from ttalkkak.ops_api.app import build_serve_app

    if tenants_json is None:
        from ttalkkak.client.config import write_private
        from ttalkkak.gateway.manifests import tenants_json as snapshot

        tenants_path = TK_HOME / "tenants.snapshot.json"
        write_private(tenants_path, snapshot(_store(db)))
    else:
        tenants_path = Path(tenants_json)
    app = build_serve_app(
        tenants_path=tenants_path,
        cost_path=Path(cost) if cost else None,
        logs=Path(logs) if logs else None,
        issuer=issuer,
        client_id=client_id,
        jwks_url=jwks_url or f"{issuer.rstrip('/')}/keys",
        logs_from_cluster=logs_from_cluster,
    )
    typer.echo(f"ttalkkak ops serve: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@ops.command()
def cost(
    run: str = typer.Option(...),
    config: str | None = typer.Option(None, help="기본 runs/<run>/cost.yaml"),
    target_output_tok_s: float | None = typer.Option(None, help="목표 출력 토큰/초"),
    seconds: int = typer.Option(60, help="부하 한 단계 시간(초)"),
) -> None:
    """전력을 재며 부하를 걸어 토큰당 원가·권장 가격·운영점을 계산합니다."""
    _need_extra("guidellm")
    from ttalkkak.cost.runner import run_cost

    run_dir = ROOT / "runs" / run
    cfg = Path(config) if config else run_dir / "cost.yaml"
    try:
        _, created = run_cost(
            run_dir, cfg, max_seconds=seconds, target_output_tok_s=target_output_tok_s
        )
    except TtalkkakError as err:
        typer.echo(f"오류: {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    if created:
        typer.echo(
            f"{cfg} 기본값으로 계산했습니다. 값을 고친 뒤 다시 실행하세요", err=True
        )
    typer.echo((run_dir / "cost.md").read_text(encoding="utf-8"))


cluster = typer.Typer(no_args_is_help=True, help="쿠버네티스에 배포·상태·삭제")
ops.add_typer(cluster, name="cluster")


@cluster.command("up")
@_tenant_cmd
def cluster_up(out: str = typer.Option(...), wait: int = 300):
    from ttalkkak.cluster.apply import apply_dir

    for name in apply_dir(Path(out), wait_s=wait):
        typer.echo(f"적용: {name}")
    typer.echo("준비됐습니다: ttalkkak ops cluster status")


@cluster.command("status")
@_tenant_cmd
def cluster_status(namespace: str = "ttalkkak"):
    from ttalkkak.cluster.apply import status

    typer.echo(f"{'파드':<44}{'준비':>6}{'재시작':>8}  상태")
    for r in status(namespace):
        typer.echo(f"{r['name']:<44}{r['ready']:>6}{r['restarts']:>8}  {r['phase']}")


@cluster.command("down")
@_tenant_cmd
def cluster_down(out: str = typer.Option(...), yes: bool = False):
    from ttalkkak.cluster.apply import down

    if not yes:
        raise TtalkkakError("모든 구성요소를 지웁니다. 계속하려면 --yes", 2)
    down(Path(out))
    typer.echo("삭제했습니다")
