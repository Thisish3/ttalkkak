# ttalkkak Stage 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 `ttalkkak ops ...`로 원가 측정, 테넌트·키·로그인, 게이트웨이(Envoy AI Gateway + llm-d + vLLM)와 관측 스택 배포, 청구서 생성을 하고, 최종 사용자는 `ttalkkak`만 쳐서 브라우저 로그인 후 바로 대화할 수 있게 한다.

**Architecture:** ttalkkak 안에 `cost/`(전력·원가 계산), `tenants/`(SQLite 테넌트·키), `auth/`(OIDC 디바이스 흐름 클라이언트), `gateway/`(쿠버네티스·관측 매니페스트 생성), `cluster/`(적용·상태), `billing/`(로그 집계), `ops_api/`(가격표·사용량 API)를 새로 둔다. 무거운 의존성은 `ops` extra로 분리하고 CLI는 사용자용 `ttalkkak`과 운영자용 `ttalkkak ops`로 나눈다. 맥에서 Task 1~11(GPU·클러스터 없이)을 TDD로 만들고, 데스크탑 k3s에서 Task 12~13으로 검증한다.

**Tech Stack:** Python 3.12, typer, pydantic, httpx, pyyaml, sqlite3, Starlette/uvicorn, guidellm 0.8.1, Envoy Gateway + Envoy AI Gateway, llm-d(Gateway API Inference Extension), Dex(또는 Keycloak), Redis, Prometheus, Grafana, OpenTelemetry Collector, Jaeger, NVIDIA DCGM exporter.

**Spec:** `docs/specs/2026-11-11-ttalkkak-stage5-design.md`

## Global Constraints

- 브랜치 `stage5`. 커밋 작성자 `Thisish3 <104387634+Thisish3@users.noreply.github.com>`, 메시지 끝 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 테스트 `.venv/bin/python -m pytest -q tests`, 린트 `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`(line-length 88). 기존 테스트(177개)는 계속 통과해야 한다.
- 사용자 메시지·오류 문구는 한국어, 코드·로그 키는 영어.
- **프롬프트·응답 본문은 어디에도 저장하지 않는다**(로그, 추적, DB).
- 키는 SHA-256 해시와 앞 8자만 저장, 원문은 발급 때 한 번만 출력.
- 자격 파일(`~/.ttalkkak/config.toml`, `~/.ttalkkak/credentials.json`)과 `tenants.db`는 권한 0600.
- 요금은 추정(원가 → 권장 가격 → 청구서 CSV). 결제·요금제·선불은 하지 않는다.
- 외부 구성요소 버전과 CRD 필드 이름은 Task 6 Step 1에서 고정하고 스펙 "환경 기록"에 적는다. 고정한 버전의 CRD와 다른 필드를 쓰지 않는다.
- 측정하지 않은 원인은 "원인 미확인"으로 쓴다. 계획과 다르게 한 결정은 `docs/stage5-ledger.md`에 `R<n>`으로.

## Review Focus

1. **로그인 중 사용자가 브라우저에서 거절하거나 코드가 만료됨**: 클라이언트가 끝없이 기다리지 않고 한국어 오류로 끝나야 한다 → Task 3 `test_device_flow_denied_and_expired`.
2. **테넌트가 없는 사용자 / 그룹이 둘인 사용자로 로그인**: 게이트웨이가 요청을 테넌트 없이 통과시키면 청구가 빠진다. 클라이언트 `whoami`와 ops API가 거절해야 한다 → Task 3 `test_whoami_requires_exactly_one_tenant`, Task 9 `test_usage_rejects_multi_tenant_token`.
3. **같은 로그가 두 번 수집되거나 기간 경계에 걸친 요청**: 중복 청구 금지, 경계는 `[from, to)` → Task 8 `test_bill_dedup_and_period_bounds`.
4. **전력 기록이 측정 구간을 다 덮지 못함**(nvidia-smi가 늦게 시작·일찍 끝남): 덮지 못한 구간이 10%를 넘으면 그 측정을 버리고 경고 → Task 4 `test_energy_requires_coverage`.
5. **연립 해가 음수·불안정**(측정 잡음): 대체식과 경고, 음수 가격 금지 → Task 5 `test_fallback_when_solution_negative`.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/ttalkkak/cli.py` | 사용자용 앱(`ttalkkak`, `login`, `logout`, `whoami`)과 `ops` 서브앱 연결, 옛 이름 별칭 |
| `src/ttalkkak/ops_cli.py` | `ttalkkak ops` 서브앱(up, chat, export, ui, cost, tenants, cluster, bill, serve) |
| `src/ttalkkak/client/config.py` | `~/.ttalkkak/` 설정·자격 파일 읽기·쓰기(0600) |
| `src/ttalkkak/client/console.py` | 최소 콘솔 대화(스트리밍, 턴별 토큰·예상 금액). 6단계에서 에이전트로 교체 |
| `src/ttalkkak/auth/device.py` | OIDC 디바이스 코드 흐름, 토큰 갱신·폐기 |
| `src/ttalkkak/cost/power.py` | nvidia-smi CSV 파싱, 구간 에너지 적분 |
| `src/ttalkkak/cost/model.py` | `CostConfig`, 토큰당 에너지·원가·가격·운영점·GPU 수 |
| `src/ttalkkak/cost/runner.py` | guidellm 부하 3종 + 전력 기록 실행, `cost.json`/`cost.md` |
| `src/ttalkkak/tenants/store.py` | SQLite 테넌트·키·salt |
| `src/ttalkkak/gateway/manifests.py` | 게이트웨이·llm-d·vLLM·Redis·Dex 매니페스트 |
| `src/ttalkkak/gateway/observability.py` | Prometheus·Grafana 대시보드·경보·OTel·Jaeger·DCGM |
| `src/ttalkkak/gateway/versions.py` | Task 6에서 고정한 버전·apiVersion 상수 |
| `src/ttalkkak/cluster/apply.py` | 번호 순 적용·대기·상태·삭제(kubectl 호출) |
| `src/ttalkkak/billing/bill.py` | 접근 로그 집계, 청구서 CSV, CloudEvents |
| `src/ttalkkak/ops_api/app.py` | `/v1/pricing`, `/v1/usage`, 관리 API |
| `src/ttalkkak/ui/static/index.html`, `src/ttalkkak/ui/app.py` | 운영 탭 4개 |
| `pyproject.toml` | 기본 의존성 최소화, `ops` extra |

---

### Task 1: CLI 분리와 설치 extra

**Files:**
- Create: `src/ttalkkak/ops_cli.py`, `src/ttalkkak/client/__init__.py`, `src/ttalkkak/client/config.py`, `tests/test_cli_split.py`
- Modify: `src/ttalkkak/cli.py`, `pyproject.toml`, `README.md`

**Interfaces:**
- Produces:
```python
# src/ttalkkak/client/config.py
from pathlib import Path
from pydantic import BaseModel

HOME = Path.home() / ".ttalkkak"

class ClientConfig(BaseModel):
    endpoint: str          # e.g. https://gw.example.com (OpenAI-compatible base, no /v1)
    issuer: str | None = None      # OIDC issuer for browser login
    client_id: str = "ttalkkak-cli"
    model: str | None = None

def load_config(home: Path = HOME) -> ClientConfig | None: ...
def save_config(cfg: ClientConfig, home: Path = HOME) -> Path: ...   # writes config.toml, chmod 0600
def write_private(path: Path, text: str) -> None: ...                 # create dir 0700, file 0600
```
- `ttalkkak.cli:app`: 인자 없이 실행하면 `client.console.main()`(Task 10에서 채움, 이 Task에서는 "아직 접속 설정이 없습니다: ttalkkak login" 안내 후 종료 코드 2). 서브명령 `login`, `logout`, `whoami`(Task 3에서 구현, 이 Task에서는 자리만), `ops`.
- `ttalkkak.ops_cli:ops`: 기존 `up`, `chat`, `export`, `ui`를 옮김. 최상위 `ttalkkak up|chat|export|ui`는 숨긴 별칭으로 남기고 실행 시 stderr에 "`ttalkkak ops up`으로 바뀌었습니다" 안내.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_cli_split.py`:
```python
import os
import stat
import subprocess
import sys

from typer.testing import CliRunner

from ttalkkak.cli import app
from ttalkkak.client.config import ClientConfig, load_config, save_config

runner = CliRunner()


def test_base_import_does_not_pull_ops_dependencies():
    # the light client must import without langchain/starlette/guidellm
    code = (
        "import sys, ttalkkak.cli;"
        "bad=[m for m in ('langchain','starlette','guidellm','huggingface_hub') if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_no_args_without_config_points_to_login(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path / ".ttalkkak")
    r = runner.invoke(app, [])
    assert r.exit_code == 2 and "ttalkkak login" in r.output


def test_ops_group_lists_operator_commands():
    r = runner.invoke(app, ["ops", "--help"])
    for name in ("up", "chat", "export", "ui"):
        assert name in r.output


def test_old_top_level_name_still_works_with_notice():
    r = runner.invoke(app, ["up", "--help"])
    assert r.exit_code == 0


def test_config_file_is_private(tmp_path):
    p = save_config(ClientConfig(endpoint="https://gw"), tmp_path)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert load_config(tmp_path).endpoint == "https://gw"
```
Run: `.venv/bin/python -m pytest -q tests/test_cli_split.py`
Expected: `ttalkkak.client` 모듈 없음으로 실패.

- [ ] **Step 2: `client/config.py`**

```python
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

HOME = Path.home() / ".ttalkkak"


class ClientConfig(BaseModel):
    endpoint: str
    issuer: str | None = None
    client_id: str = "ttalkkak-cli"
    model: str | None = None


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def save_config(cfg: ClientConfig, home: Path = HOME) -> Path:
    lines = [f'{k} = "{v}"' for k, v in cfg.model_dump().items() if v is not None]
    path = home / "config.toml"
    write_private(path, "\n".join(lines) + "\n")
    return path


def load_config(home: Path | None = None) -> ClientConfig | None:
    from ttalkkak.client import config as me

    path = (home or me.HOME) / "config.toml"
    if not path.is_file():
        return None
    return ClientConfig(**tomllib.loads(path.read_text(encoding="utf-8")))
```

- [ ] **Step 3: `ops_cli.py`로 옮기고 `cli.py`를 가볍게**

- `cli.py`의 `up`, `chat`, `export`, `ui` 본문을 `ops_cli.py`의 `ops = typer.Typer(no_args_is_help=True, help="운영자용 명령")`로 옮긴다. 무거운 import(`httpx`는 가벼우니 유지, `ttalkkak.launch.docker`, `ttalkkak.pipeline`, `ttalkkak.probe`, `ttalkkak.verify.*`)는 각 명령 함수 안으로 옮긴다.
- `cli.py`:
```python
import typer

app = typer.Typer(invoke_without_command=True, add_completion=False)


@app.callback()
def main(ctx: typer.Context) -> None:
    """ttalkkak: 오픈소스 모델 기반 콘솔 에이전트 (운영자는 `ttalkkak ops`)."""
    if ctx.invoked_subcommand is None:
        from ttalkkak.client.console import main as console_main

        raise typer.Exit(console_main())


def _ops():
    from ttalkkak.ops_cli import ops

    return ops


app.add_typer(_ops(), name="ops")


def _alias(name: str):
    def run(ctx: typer.Context):
        typer.echo(f"알림: `ttalkkak {name}`은 `ttalkkak ops {name}`으로 바뀌었습니다", err=True)
        from typer.main import get_command

        get_command(_ops()).main(args=[name, *ctx.args], prog_name="ttalkkak ops")

    return run


for _n in ("up", "chat", "export", "ui"):
    app.command(_n, hidden=True, context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []})(_alias(_n))
```
`ops_cli.py`는 모듈 최상위에서 typer와 pathlib만 import한다(그래야 `test_base_import...`가 통과).
- `client/console.py` 임시 본문:
```python
import typer

from ttalkkak.client.config import load_config


def main() -> int:
    if load_config() is None:
        typer.echo("아직 접속 설정이 없습니다: ttalkkak login", err=True)
        return 2
    typer.echo("콘솔 대화는 Task 10에서 붙습니다", err=True)
    return 0
```

- [ ] **Step 4: `pyproject.toml` extra 분리**

```toml
dependencies = ["pydantic==2.14.3", "httpx==0.28.1", "typer==0.27.3", "pyyaml"]
[project.optional-dependencies]
ops = ["huggingface-hub==2.4.0", "langchain==1.4.9", "langgraph==1.2.20", "langchain-openai",
       "fastmcp==4.1.6", "starlette==1.10.0", "uvicorn==0.54.0"]
measure = ["guidellm==0.8.1", "lm-eval[api]==0.4.13"]
```
`uv lock && uv sync --all-extras`로 개발 환경을 맞춘다.

- [ ] **Step 5: 테스트·린트**

Run: `.venv/bin/python -m pytest -q tests && .venv/bin/ruff check src tests`
Expected: 기존 177개 + 새 5개 통과. 기존 CLI 테스트가 `ttalkkak up` 경로를 쓰면 `ops up`으로 바꾼다.

- [ ] **Step 6: 설치 경험 확인과 Commit**

Run: `uv build && uv tool install --force dist/ttalkkak-*.whl && ttalkkak --help && ttalkkak ops --help | head -5; uv tool uninstall ttalkkak`
Expected: 기본 설치로 `ttalkkak --help`가 뜨고, `ops`의 무거운 명령은 실행 시 "ttalkkak[ops]가 필요합니다" 안내(ImportError를 잡아 안내하도록 `ops_cli`의 각 명령 첫 줄에서 처리).
README 설치 절: `uv tool install ttalkkak`(사용자), `uv tool install "ttalkkak[ops,measure]"`(운영자).
```bash
git add -A && git commit -m "Split the CLI into the user console and ttalkkak ops, move heavy deps to extras"
```

---

### Task 2: 테넌트·키 저장소와 `ops tenants`

**Files:**
- Create: `src/ttalkkak/tenants/__init__.py`, `src/ttalkkak/tenants/store.py`, `tests/test_tenants.py`
- Modify: `src/ttalkkak/ops_cli.py`

**Interfaces:**
- Produces:
```python
# src/ttalkkak/tenants/store.py
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass
class Tenant:
    id: str; name: str; token_limit_per_min: int | None; cache_mode: str; salt_version: int

@dataclass
class KeyRow:
    id: int; tenant_id: str; prefix: str; created_at: datetime; expires_at: datetime | None; revoked_at: datetime | None

class TenantStore:
    def __init__(self, path: Path): ...                      # creates schema, chmod 0600
    def add_tenant(self, tid: str, name: str) -> Tenant: ...  # tid: ^[a-z0-9-]{2,32}$
    def list_tenants(self) -> list[Tenant]: ...
    def issue_key(self, tid: str) -> str: ...                 # returns raw key once: tk_<tid>_<32 urlsafe>
    def rotate_key(self, tid: str, grace_hours: int = 24) -> str: ...
    def revoke_key(self, prefix: str) -> None: ...
    def active_keys(self, now: datetime | None = None) -> list[KeyRow]: ...
    def key_hashes(self, now: datetime | None = None) -> dict[str, str]: ...  # sha256 hex -> tenant id
    def resolve(self, raw_key: str, now: datetime | None = None) -> str | None: ...  # tenant id
    def set_limit(self, tid: str, per_min: int | None) -> None: ...
    def set_cache(self, tid: str, mode: str) -> None: ...     # "isolated" | "shared"
    def rotate_salt(self, tid: str) -> int: ...
    def cache_salt(self, tid: str) -> str | None: ...         # None when shared; sha256(f"{tid}:{v}")[:32]
```

- [ ] **Step 1: 실패하는 테스트**

```python
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.tenants.store import TenantStore

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    s = TenantStore(tmp_path / "tenants.db")
    s.add_tenant("acme", "Acme")
    return s


def test_db_is_private(tmp_path, st):
    assert stat.S_IMODE(os.stat(tmp_path / "tenants.db").st_mode) == 0o600


def test_raw_key_is_never_stored(tmp_path, st):
    raw = st.issue_key("acme")
    assert raw.startswith("tk_acme_") and raw not in (tmp_path / "tenants.db").read_bytes().decode("latin-1")
    assert st.resolve(raw) == "acme" and st.resolve(raw + "x") is None


def test_rotation_keeps_old_key_for_grace_period(st):
    old = st.issue_key("acme")
    new = st.rotate_key("acme", grace_hours=24)
    assert st.resolve(old, NOW) == "acme" and st.resolve(new, NOW) == "acme"
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert st.resolve(old, later) is None and st.resolve(new, later) == "acme"


def test_revoke_is_immediate(st):
    raw = st.issue_key("acme")
    st.revoke_key(raw[:8 + len("tk_acme_")])
    assert st.resolve(raw) is None


def test_cache_salt_isolated_by_default_and_rotates(st):
    s1 = st.cache_salt("acme")
    assert s1 and len(s1) == 32
    st.rotate_salt("acme")
    assert st.cache_salt("acme") != s1
    st.set_cache("acme", "shared")
    assert st.cache_salt("acme") is None


def test_bad_tenant_id_and_unknown_tenant(st):
    with pytest.raises(TtalkkakError):
        st.add_tenant("Bad Id", "x")
    with pytest.raises(TtalkkakError):
        st.issue_key("nobody")
```
(`revoke_key`의 인자는 키 앞부분 `prefix`다. `prefix`는 `tk_<tid>_` 뒤 8자까지를 포함하는 문자열로 정의하고 `KeyRow.prefix`에 그대로 저장한다.)

Run: `.venv/bin/python -m pytest -q tests/test_tenants.py` → 모듈 없음으로 실패.

- [ ] **Step 2: 구현**

`sqlite3` 표준 라이브러리. 스키마:
```sql
CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
  token_limit_per_min INTEGER, cache_mode TEXT NOT NULL DEFAULT 'isolated', salt_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS keys(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL REFERENCES tenants(id),
  prefix TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT);
```
키 생성: `f"tk_{tid}_{secrets.token_urlsafe(24)}"`, 저장 `hashlib.sha256(raw.encode()).hexdigest()`, `prefix = raw[: len(f"tk_{tid}_") + 8]`. `resolve`는 해시로 찾고 `revoked_at`이 없고 `expires_at`이 `now`보다 뒤(또는 없음)인지 본다. 시간은 UTC ISO 문자열. 파일 생성 직후 `os.chmod(path, 0o600)`.

- [ ] **Step 3: `ops tenants` 명령**

`ops_cli.py`에 `tenants = typer.Typer(help="테넌트와 키")`, `ops.add_typer(tenants, name="tenants")`. 명령: `add <id> --name`, `list`(표: id, 이름, 한도, 캐시, 활성 키 수), `key-issue <id>`(원문 출력 + "다시 볼 수 없습니다"), `key-rotate <id> [--grace-hours 24]`, `key-revoke <prefix>`, `set-limit <id> <per_min|none>`, `set-cache <id> isolated|shared`, `rotate-salt <id>`. 저장소 경로 기본 `~/.ttalkkak/tenants.db`, `--db`로 변경. CliRunner 테스트를 `tests/test_tenants.py`에 2개 추가(add→key-issue 출력 형식, list 표).

- [ ] **Step 4: 테스트와 Commit**

Run: `.venv/bin/python -m pytest -q tests && .venv/bin/ruff check src tests`
```bash
git add -A && git commit -m "Add the tenant and API key store with ttalkkak ops tenants"
```

---

### Task 3: 브라우저 로그인(OIDC 디바이스 흐름)과 `login`/`logout`/`whoami`

**Files:**
- Create: `src/ttalkkak/auth/__init__.py`, `src/ttalkkak/auth/device.py`, `tests/fake_oidc.py`, `tests/test_auth_device.py`
- Modify: `src/ttalkkak/cli.py`

**Interfaces:**
- Consumes: `ClientConfig`, `write_private`, `HOME`(Task 1).
- Produces:
```python
# src/ttalkkak/auth/device.py
from dataclasses import dataclass

@dataclass
class Tokens:
    access_token: str; refresh_token: str | None; expires_at: float; id_claims: dict

class DeviceLogin:
    def __init__(self, issuer: str, client_id: str, http: "httpx.Client", sleep=time.sleep, now=time.time): ...
    def discover(self) -> dict: ...                       # GET {issuer}/.well-known/openid-configuration
    def start(self) -> dict: ...                          # device_code, user_code, verification_uri(_complete), interval, expires_in
    def poll(self, start: dict) -> Tokens: ...            # handles authorization_pending, slow_down, access_denied, expired_token
    def refresh(self, t: Tokens) -> Tokens: ...
    def revoke(self, t: Tokens) -> None: ...              # revocation_endpoint if advertised

def tenant_of(claims: dict) -> str: ...                    # exactly one group -> tenant id, else TtalkkakError
def save_tokens(t: Tokens, home: Path = HOME) -> Path: ...  # credentials.json, 0600
def load_tokens(home: Path = HOME) -> Tokens | None: ...
def valid_access_token(cfg: ClientConfig, http, home=HOME, now=time.time) -> str: ...  # refresh when < 60 s left
```
`id_claims`는 ID 토큰의 payload를 base64url로 풀어 얻는다(서명 검증은 게이트웨이가 한다. 클라이언트는 표시용으로만 쓴다고 주석에 명시).

- [ ] **Step 1: 가짜 OIDC 서버 `tests/fake_oidc.py`**

Starlette 앱 + `httpx.MockTransport` 대신 `starlette.testclient.TestClient`를 `httpx.Client`처럼 쓴다. 상태로 `pending_polls`(승인 전 pending을 몇 번 줄지), `deny`, `expire`, `slow_down_once`를 받는다. 엔드포인트: `/.well-known/openid-configuration`, `/device/code`(POST), `/token`(POST, `grant_type`이 device_code면 상태대로 `authorization_pending`/`slow_down`/`access_denied`/`expired_token` 400 응답 또는 토큰; refresh_token이면 새 토큰), `/revoke`. ID 토큰은 `header.payload.sig` 형식으로 payload에 `{"sub": "u1", "email": "u1@acme", "groups": [...]}`.

- [ ] **Step 2: 실패하는 테스트 `tests/test_auth_device.py`**

```python
import pytest

from fake_oidc import make_oidc
from ttalkkak.auth.device import DeviceLogin, load_tokens, save_tokens, tenant_of
from ttalkkak.errors import TtalkkakError


def login(**state):
    http, srv = make_oidc(**state)
    slept = []
    d = DeviceLogin("https://idp.test", "ttalkkak-cli", http, sleep=slept.append)
    return d, d.start(), slept, srv


def test_device_flow_succeeds_after_pending():
    d, st, slept, _ = login(pending_polls=2, groups=["acme"])
    t = d.poll(st)
    assert t.access_token and tenant_of(t.id_claims) == "acme" and len(slept) == 3


def test_slow_down_increases_interval():
    d, st, slept, _ = login(pending_polls=0, slow_down_once=True, groups=["acme"])
    d.poll(st)
    assert slept[1] >= slept[0] + 5


def test_device_flow_denied_and_expired():
    d, st, _, _ = login(deny=True)
    with pytest.raises(TtalkkakError, match="거절"):
        d.poll(st)
    d, st, _, _ = login(expire=True)
    with pytest.raises(TtalkkakError, match="만료"):
        d.poll(st)


def test_whoami_requires_exactly_one_tenant():
    with pytest.raises(TtalkkakError):
        tenant_of({"sub": "u", "groups": []})
    with pytest.raises(TtalkkakError):
        tenant_of({"sub": "u", "groups": ["a", "b"]})


def test_refresh_and_revoke(tmp_path):
    d, st, _, srv = login(groups=["acme"])
    t = d.poll(st)
    t2 = d.refresh(t)
    assert t2.access_token != t.access_token
    d.revoke(t2)
    assert srv.revoked == [t2.refresh_token]


def test_tokens_file_is_private(tmp_path):
    import os, stat
    d, st, _, _ = login(groups=["acme"])
    p = save_tokens(d.poll(st), tmp_path)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and load_tokens(tmp_path).access_token
```
Run → 모듈 없음으로 실패.

- [ ] **Step 3: `device.py` 구현**

RFC 8628 규칙: `interval` 기본 5초, `slow_down`이면 5초 증가, `expires_in`을 넘으면 만료. 오류 메시지: `access_denied` → "브라우저에서 로그인을 거절했습니다", `expired_token`/시간 초과 → "로그인 코드가 만료됐습니다. 다시 `ttalkkak login`". 토큰 요청 본문은 `application/x-www-form-urlencoded`. scope는 `openid profile email groups offline_access`.

- [ ] **Step 4: `login`/`logout`/`whoami` 명령**

`cli.py`:
- `login [--endpoint URL] [--issuer URL]`: 설정이 없으면 두 값을 묻는다(`typer.prompt`). `issuer`를 안 주면 `GET {endpoint}/v1/auth-config`(Task 9가 제공, `{"issuer": ..., "client_id": ...}`)에서 가져온다. 디바이스 흐름 시작 → `코드: ABCD-1234`, 주소 출력, `webbrowser.open(verification_uri_complete)` 시도 → poll → `tenant_of` 확인 → 저장 → "로그인했습니다: <email> (테넌트 acme)".
- `logout`: 토큰 폐기 시도 후 `credentials.json` 삭제.
- `whoami`: 이메일, 테넌트, 접근 토큰 남은 시간.
CliRunner 테스트 2개(가짜 OIDC 주입, `ttalkkak.cli`의 HTTP 클라이언트 생성 함수를 monkeypatch).

- [ ] **Step 5: 테스트와 Commit**

```bash
git add -A && git commit -m "Add browser login over the OIDC device flow with login, logout and whoami"
```

---

### Task 4: 전력 기록과 구간 에너지

**Files:**
- Create: `src/ttalkkak/cost/__init__.py`, `src/ttalkkak/cost/power.py`, `tests/test_cost_power.py`, `tests/fixtures/power/nvsmi.csv`

**Interfaces:**
- Produces:
```python
@dataclass
class Sample: t: float; watts: float; sm_mhz: float | None; temp_c: float | None
def parse_nvsmi_csv(text: str) -> list[Sample]: ...   # nvidia-smi --query-gpu=timestamp,power.draw,clocks.sm,temperature.gpu --format=csv
def energy_j(samples: list[Sample], t0: float, t1: float, min_coverage: float = 0.9) -> float: ...
POWER_CMD: list[str]   # the nvidia-smi command with -lms 500
```

- [ ] **Step 1: 테스트**

```python
import pytest

from ttalkkak.cost.power import Sample, energy_j, parse_nvsmi_csv
from ttalkkak.errors import TtalkkakError

CSV = """timestamp, power.draw [W], clocks.current.sm [MHz], temperature.gpu
2026/11/11 12:00:00.000, 100.00 W, 1500 MHz, 50
2026/11/11 12:00:01.000, 200.00 W, 1500 MHz, 51
2026/11/11 12:00:02.000, 200.00 W, 1500 MHz, 52
"""


def test_parse_and_trapezoid():
    s = parse_nvsmi_csv(CSV)
    assert [x.watts for x in s] == [100, 200, 200]
    t0 = s[0].t
    assert energy_j(s, t0, t0 + 2) == pytest.approx(150 + 200)


def test_partial_interval_is_interpolated():
    s = parse_nvsmi_csv(CSV)
    t0 = s[0].t
    assert energy_j(s, t0 + 0.5, t0 + 1.5) == pytest.approx(162.5 + 100, rel=1e-6)


def test_energy_requires_coverage():
    s = parse_nvsmi_csv(CSV)
    t0 = s[0].t
    with pytest.raises(TtalkkakError, match="전력 기록"):
        energy_j(s, t0 - 5, t0 + 2)


def test_bad_rows_are_skipped():
    s = parse_nvsmi_csv(CSV + "2026/11/11 12:00:03.000, [N/A], 1500 MHz, 52\n")
    assert len(s) == 3
```
(타임스탬프는 로컬 시각 문자열이다. `parse_nvsmi_csv`는 `datetime.strptime(..., "%Y/%m/%d %H:%M:%S.%f").timestamp()`로 바꾼다. guidellm의 `start_time`은 epoch이므로 같은 기계에서 잰다는 전제를 주석에 적는다.)

- [ ] **Step 2: 구현**

사다리꼴 적분, 구간 경계는 선형 보간. 덮인 시간 `[max(t0, first), min(t1, last)]`이 `(t1 - t0) × min_coverage`보다 짧으면 `TtalkkakError("전력 기록이 측정 구간의 90%를 덮지 못했습니다")`.

- [ ] **Step 3: 테스트와 Commit**

```bash
git add -A && git commit -m "Integrate GPU power samples over a measured interval"
```

---

### Task 5: 원가 모델

**Files:**
- Create: `src/ttalkkak/cost/model.py`, `tests/test_cost_model.py`

**Interfaces:**
- Produces:
```python
class Slo(BaseModel): ttft_p99_ms: float = 1000; itl_p99_ms: float = 100
class CostConfig(BaseModel):
    gpu_price_krw: float = 600000; amortization_years: float = 3; utilization: float = 0.6
    electricity_krw_per_kwh: float = 150; pue: float = 1.4; margin: float = 0.3
    slo: Slo = Slo(); target_output_tok_s: float | None = None
class Point(BaseModel):   # one concurrency level of the decode-heavy sweep
    streams: int; output_tok_s: float; ttft_p99_ms: float; itl_p99_ms: float
class EnergyRun(BaseModel): energy_j: float; input_tokens: int; cached_tokens: int; output_tokens: int
class Prices(BaseModel): input: float; cached_input: float; output: float   # KRW per 1M tokens
class CostResult(BaseModel):
    e_in_j: float; e_cached_j: float; e_out_j: float; warnings: list[str]
    operating_point: Point | None; gpus_needed: int | None
    cost_krw_per_mtok: Prices; price_krw_per_mtok: Prices; config: CostConfig

def solve_energy(a: EnergyRun, b: EnergyRun, c: EnergyRun | None) -> tuple[float, float, float, list[str]]: ...
def operating_point(points: list[Point], slo: Slo) -> Point | None: ...
def compute(cfg: CostConfig, a: EnergyRun, b: EnergyRun, c: EnergyRun | None, points: list[Point]) -> CostResult: ...
def load_or_create_config(path: Path) -> tuple[CostConfig, bool]: ...   # bool: created defaults
```

- [ ] **Step 1: 테스트**

```python
import pytest

from ttalkkak.cost.model import CostConfig, EnergyRun, Point, Slo, compute, operating_point, solve_energy

A = EnergyRun(energy_j=1*256*10 + 5*128*10, input_tokens=256*10, cached_tokens=0, output_tokens=128*10)
B = EnergyRun(energy_j=1*2048*10 + 5*1*10, input_tokens=2048*10, cached_tokens=0, output_tokens=10)


def test_solve_energy_recovers_known_values():
    e_in, e_cached, e_out, w = solve_energy(A, B, None)
    assert e_in == pytest.approx(1) and e_out == pytest.approx(5) and w == []


def test_fallback_when_solution_negative():
    bad_b = EnergyRun(energy_j=10, input_tokens=2048*10, cached_tokens=0, output_tokens=10)
    e_in, _, e_out, w = solve_energy(A, bad_b, None)
    assert e_in >= 0 and e_out >= 0 and w


def test_cached_tokens_energy_from_third_run():
    C = EnergyRun(energy_j=1*256*10 + 0.1*1792*10 + 5*128*10, input_tokens=256*10, cached_tokens=1792*10, output_tokens=128*10)
    _, e_cached, _, _ = solve_energy(A, B, C)
    assert e_cached == pytest.approx(0.1)


def test_operating_point_is_largest_within_slo():
    pts = [Point(streams=s, output_tok_s=100*s, ttft_p99_ms=100*s, itl_p99_ms=20*s) for s in (1, 2, 4, 8)]
    assert operating_point(pts, Slo(ttft_p99_ms=500, itl_p99_ms=100)).streams == 4
    assert operating_point(pts, Slo(ttft_p99_ms=50, itl_p99_ms=100)) is None


def test_prices_and_gpus_needed():
    pts = [Point(streams=4, output_tok_s=400, ttft_p99_ms=100, itl_p99_ms=50)]
    cfg = CostConfig(target_output_tok_s=1000)
    r = compute(cfg, A, B, None, pts)
    # electricity per output token: 5 J / 3.6e6 * 150 * 1.4 KRW -> per 1M tokens
    elec_out = 5 / 3.6e6 * 150 * 1.4 * 1e6
    assert r.cost_krw_per_mtok.output > elec_out
    assert r.price_krw_per_mtok.output == pytest.approx(r.cost_krw_per_mtok.output * 1.3)
    assert r.gpus_needed == 3


def test_no_operating_point_means_no_gpu_count_and_a_warning():
    pts = [Point(streams=1, output_tok_s=50, ttft_p99_ms=5000, itl_p99_ms=500)]
    r = compute(CostConfig(target_output_tok_s=1000), A, B, None, pts)
    assert r.operating_point is None and r.gpus_needed is None and any("SLO" in w for w in r.warnings)
```

- [ ] **Step 2: 구현**

- `solve_energy`: 2×2 크래머 공식. 행렬식 절댓값이 `1e-9 × |a11·a22|`보다 작거나 해가 음수면 대체식(스펙 3.2)과 경고. C가 있으면 `e_cached = max(0, (E_C − e_in·N_in,C − e_out·N_out,C) / N_cached,C)`, 음수였으면 경고.
- 전기료/토큰(원) = `e / 3.6e6 × electricity × pue`.
- 상각/초 = `gpu_price / (years × 365 × 86400 × utilization)`. 운영점이 있으면 `상각/출력토큰 = 상각/초 ÷ output_tok_s`, 이를 에너지 비율(`e_in/e_out`, `e_cached/e_out`)로 입력·캐시 입력에 나눠 준다. 운영점이 없으면 가장 낮은 동시성 점을 쓰고 경고 "SLO를 지키는 운영점이 없어 동시성 1 기준으로 계산".
- 가격 = 원가 × (1 + margin), 모두 100만 토큰 단위.
- `gpus_needed = ceil(target / operating_point.output_tok_s)`(운영점과 목표가 둘 다 있을 때).
- `load_or_create_config`: 파일이 없으면 기본값 YAML을 쓰고 `(cfg, True)`.

- [ ] **Step 3: 테스트와 Commit**

```bash
git add -A && git commit -m "Add the cost model: per-token energy, prices, SLO operating point and GPU count"
```

---

### Task 6: 외부 구성요소 버전 고정과 매니페스트 생성

**Files:**
- Create: `src/ttalkkak/gateway/__init__.py`, `src/ttalkkak/gateway/versions.py`, `src/ttalkkak/gateway/manifests.py`, `tests/test_gateway_manifests.py`, `tests/fixtures/gateway/*.yaml`(기대 파일)
- Modify: 스펙 "환경 기록"

**Interfaces:**
- Consumes: `load_run`, `engine_args`, `resource_name`(기존 `export/common.py`), `TenantStore`(Task 2), `CostResult`(Task 5).
- Produces:
```python
# src/ttalkkak/gateway/manifests.py
class GatewayOptions(BaseModel):
    namespace: str = "ttalkkak"; replicas: int = 1; issuer: str | None = None   # None -> deploy Dex
    hostname: str = "ttalkkak.local"; image: str | None = None
def render_stack(run_dir: Path, store: TenantStore, cost: CostResult | None, opt: GatewayOptions) -> dict[str, str]:
    """file name -> YAML text: 00-namespace.yaml, 10-redis.yaml, 15-dex.yaml (if issuer is None),
    20-vllm.yaml, 30-llmd.yaml, 40-gateway.yaml, README.md"""
```

- [ ] **Step 1: 버전 고정 (맥, 문서·CRD 기준)**

각 구성요소의 최신 안정 릴리스를 GitHub 릴리스 페이지에서 고르고, 릴리스에 포함된 CRD YAML을 받아 실제 필드 이름을 확인한다:
```bash
mkdir -p /tmp/crds && cd /tmp/crds
# 예: Envoy AI Gateway, Envoy Gateway, Gateway API Inference Extension 릴리스의 CRD 파일 URL을 릴리스 노트에서 찾아 curl -LO
grep -n "llmRequestCosts\|metadataKey\|apiKeyAuth\|jwt:\|rateLimit\|InferencePool" *.yaml | head -50
```
`src/ttalkkak/gateway/versions.py`에 상수로 적는다:
```python
ENVOY_GATEWAY = "<버전>"
ENVOY_AI_GATEWAY = "<버전>"
AI_GATEWAY_API = "aigateway.envoyproxy.io/<v1alpha1|v1beta1>"
GATEWAY_API_INFERENCE_EXT = "<버전>"
INFERENCE_POOL_API = "inference.networking.k8s.io/<v1|v1alpha2>"
DEX_IMAGE = "ghcr.io/dexidp/dex:<버전>"
REDIS_IMAGE = "redis:<버전>"
```
값은 이 Step에서 실제로 확인한 것으로 채운다(꺾쇠 그대로 커밋 금지). 같은 내용을 스펙 "환경 기록"에 "문서·CRD 기준(데스크탑 미검증)"으로 적는다. 두 가지를 이 Step에서 함께 결정해 LEDGER에 적는다:
- **cache_salt 주입 방법**: 고정한 버전에서 가능한 순서로 (1) Envoy AI Gateway의 요청 본문 변경 기능, (2) Envoy Gateway `EnvoyExtensionPolicy`의 Lua, (3) `ops serve`(Task 9)가 ext_proc 서비스로 주입.
- **JWT와 API 키를 한 라우트에 같이 받는 방법**: SecurityPolicy가 둘을 OR로 허용하는지. 안 되면 `/v1`(JWT)과 `/v1-key`(API 키) 두 경로로 나누고, 클라이언트는 JWT 경로를 쓴다.
- **Dex 디바이스 흐름 지원 여부**: Dex 문서·릴리스 노트에서 `device_code` 지원 버전을 확인. 미지원이면 Keycloak으로 바꾸고 `15-dex.yaml`을 `15-keycloak.yaml`로.

- [ ] **Step 2: 실패하는 테스트**

```python
import yaml
from pathlib import Path

from ttalkkak.gateway.manifests import GatewayOptions, render_stack
from ttalkkak.tenants.store import TenantStore

RUN = Path(__file__).parent / "fixtures/runs/vllm-nvidia"


def all_docs(files):
    return {n: list(yaml.safe_load_all(t)) for n, t in files.items() if n.endswith(".yaml")}


def stack(tmp_path, **kw):
    st = TenantStore(tmp_path / "t.db")
    st.add_tenant("acme", "Acme"); st.issue_key("acme"); st.set_limit("acme", 6000)
    st.add_tenant("beta", "Beta"); st.set_cache("beta", "shared")
    return all_docs(render_stack(RUN, st, None, GatewayOptions(**kw)))


def test_files_and_order(tmp_path):
    d = stack(tmp_path)
    assert sorted(d) == ["00-namespace.yaml", "10-redis.yaml", "15-dex.yaml", "20-vllm.yaml", "30-llmd.yaml", "40-gateway.yaml"]


def test_vllm_replicas_and_args(tmp_path):
    dep = next(x for x in stack(tmp_path, replicas=2)["20-vllm.yaml"] if x["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 2
    args = dep["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--kv-cache-dtype" in args


def test_gateway_records_token_costs_and_limits_per_tenant(tmp_path):
    gw = stack(tmp_path)["40-gateway.yaml"]
    kinds = {x["kind"] for x in gw}
    assert {"Gateway", "AIGatewayRoute", "SecurityPolicy", "BackendTrafficPolicy"} <= kinds
    route = next(x for x in gw if x["kind"] == "AIGatewayRoute")
    names = {c["metadataKey"] for c in route["spec"]["llmRequestCosts"]}
    assert {"input_tokens", "cached_input_tokens", "output_tokens"} <= names


def test_api_key_secret_holds_hashes_only(tmp_path):
    text = "\n".join(yaml.safe_dump_all(v) for v in stack(tmp_path).values())
    assert "tk_acme_" not in text


def test_external_issuer_skips_dex(tmp_path):
    assert "15-dex.yaml" not in stack(tmp_path, issuer="https://idp.example.com")
```
(`llmRequestCosts`의 키 이름(`metadataKey`)은 Step 1에서 확인한 CRD 필드로 맞춘다. 다르면 테스트도 그 이름으로 고친다.)

Run → 모듈 없음으로 실패.

- [ ] **Step 3: 구현**

- `20-vllm.yaml`: 기존 `export/k8s.py`의 Deployment·Service 렌더를 재사용하고 `replicas`, Prometheus 수집 주석(`prometheus.io/scrape: "true"`, `prometheus.io/port: "8000"`), 캐시 토큰을 usage에 넣는 vLLM 옵션(이름은 Step 1에서 vLLM 문서로 확인해 `versions.py`에 `VLLM_CACHED_USAGE_FLAG`로)을 붙인다. KV 캐시 오프로드 옵션은 넣지 않는다.
- `30-llmd.yaml`: InferencePool(선택자 = vLLM 파드 라벨, 포트 8000)과 EPP Deployment·Service. EPP 이미지와 설정은 고정한 Inference Extension 버전의 예제 매니페스트를 따른다.
- `40-gateway.yaml`: GatewayClass(Envoy Gateway), Gateway(HTTP 80, `hostname`), AIGatewayRoute(백엔드 = InferencePool, `llmRequestCosts` 3종), SecurityPolicy(JWT: 인증 서버 JWKS, `groups` 클레임 → `x-ttalkkak-tenant` 헤더, `sub` → `x-ttalkkak-subject`; API 키: Secret `ttalkkak-api-keys`의 해시 → 테넌트), BackendTrafficPolicy(테넌트별 분당 토큰 한도, 요청 비용 0·응답 비용 = output+input 토큰 메타데이터, Redis 전역 rate limit), cache_salt 주입(Step 1 결정), 접근 로그 형식(JSON: `ts, request_id, tenant, subject, model, status, input_tokens, cached_input_tokens, output_tokens, duration_ms`, 본문 필드 없음).
- `15-dex.yaml`: Dex ConfigMap(정적 클라이언트 `ttalkkak-cli` public + device grant, 정적 사용자는 비워 두고 `ops tenants user-add`가 채움), Deployment, Service.
- `10-redis.yaml`: Redis Deployment·Service(인증 비밀번호 Secret, 스펙의 "Redis 자격을 ConfigMap에 두지 말 것" 주의).
- `README.md`: 각 파일 역할과 수동 적용 순서, 필요한 사전 설치(Envoy Gateway·AI Gateway Helm 차트, Inference Extension CRD).

- [ ] **Step 4: `ops export gateway`와 `ops tenants user-add/user-remove`**

- `ops export gateway --run <run> --out <dir> [--replicas N] [--issuer URL] [--cost runs/<run>/cost.json]`: cost.json이 있으면 `replicas`의 기본값을 `gpus_needed`로.
- `ops tenants user-add <tenant> <email> [--password-stdin]`: Dex 정적 사용자(bcrypt 해시)를 `~/.ttalkkak/dex-users.yaml`에 추가하고, `export gateway`가 이 파일을 15-dex.yaml에 넣는다. bcrypt는 `ops` extra에 `bcrypt` 추가.

- [ ] **Step 5: 테스트와 Commit**

```bash
git add -A && git commit -m "Pin gateway component versions and render the gateway, llm-d, vLLM, Redis and Dex manifests"
```

---

### Task 7: 관측 스택 생성 (Prometheus, Grafana, 경보, OTel, Jaeger, GPU exporter)

**Files:**
- Create: `src/ttalkkak/gateway/observability.py`, `src/ttalkkak/gateway/dashboards/gpu.json`, `serving.json`, `usage.json`, `tests/test_observability.py`

**Interfaces:**
- Consumes: `GatewayOptions`, `CostResult`(가격·전기료를 대시보드 상수로).
- Produces: `render_observability(cost: CostResult | None, opt: GatewayOptions) -> dict[str, str]` → `50-observability.yaml`, `grafana/gpu.json`, `grafana/serving.json`, `grafana/usage.json`, `alerts.yaml`. `render_stack`의 결과에 합쳐진다.

- [ ] **Step 1: 실패하는 테스트**

```python
import json
import re

from ttalkkak.gateway.observability import render_observability
from ttalkkak.gateway.manifests import GatewayOptions

METRICS = {  # names the dashboards may use; Task 12 replaces this with names seen on the desktop
    "vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:time_to_first_token_seconds_bucket",
    "vllm:inter_token_latency_seconds_bucket", "vllm:generation_tokens_total", "vllm:prompt_tokens_total",
    "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_POWER_USAGE", "DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_GPU_TEMP",
    "ttalkkak_tokens_total", "ttalkkak_requests_total",
}


def test_dashboards_only_use_known_metrics():
    files = render_observability(None, GatewayOptions())
    for name in ("grafana/gpu.json", "grafana/serving.json", "grafana/usage.json"):
        d = json.loads(files[name])
        exprs = [t["expr"] for p in d["panels"] for t in p.get("targets", [])]
        used = {m for e in exprs for m in re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", e) if m in METRICS or m.startswith(("vllm:", "DCGM_", "ttalkkak_"))}
        assert used <= METRICS, used - METRICS


def test_usage_dashboard_uses_cost_prices():
    from ttalkkak.cost.model import CostConfig, CostResult, Prices
    p = Prices(input=100, cached_input=10, output=500)
    cost = CostResult(e_in_j=1, e_cached_j=0.1, e_out_j=5, warnings=[], operating_point=None, gpus_needed=None,
                      cost_krw_per_mtok=p, price_krw_per_mtok=p, config=CostConfig())
    text = render_observability(cost, GatewayOptions())["grafana/usage.json"]
    assert "500" in text and "150" in text  # output price and electricity KRW/kWh


def test_alerts_cover_slo_kv_quota_temp_down():
    text = render_observability(None, GatewayOptions())["alerts.yaml"]
    for key in ("TtftSloBreach", "ItlSloBreach", "KvCacheHigh", "TenantQuota80", "GpuHot", "EngineDown"):
        assert key in text
```
`ttalkkak_tokens_total{tenant,model,kind}`와 `ttalkkak_requests_total{tenant,status}`는 Task 9의 `ops serve`가 게이트웨이 접근 로그를 읽어 내보내는 지표다(게이트웨이 지표에 테넌트 라벨이 없을 수 있어서, 스펙 7.1).

- [ ] **Step 2: 구현**

- `50-observability.yaml`: Prometheus(ConfigMap: vLLM·EPP·게이트웨이·DCGM·ops-serve 수집, 보관 2일), Grafana(대시보드·데이터소스·경보 프로비저닝 ConfigMap, 익명 보기 끄고 관리자 비밀번호 Secret), OTel Collector(OTLP 수신 → Jaeger), Jaeger all-in-one, DCGM exporter DaemonSet(이미지 버전은 Task 6 versions.py에 `DCGM_EXPORTER_IMAGE` 추가).
- 대시보드 JSON: 스펙 7.1의 패널. 금액 패널은 PromQL로 `sum by (tenant)(increase(ttalkkak_tokens_total{kind="output"}[1d])) * <출력가>/1e6` 형태, 전기료 패널은 `sum(DCGM_FI_DEV_POWER_USAGE) * <원/kWh> * <PUE> / 1000 / 3600`(원/초)을 누적.
- 경보: 스펙 7.1의 6종, 지속 시간 5분.

- [ ] **Step 3: 테스트와 Commit**

```bash
git add -A && git commit -m "Render the observability stack: Prometheus, Grafana dashboards and alerts, OTel and Jaeger"
```

---

### Task 8: 청구 (`ops bill`)

**Files:**
- Create: `src/ttalkkak/billing/__init__.py`, `src/ttalkkak/billing/bill.py`, `tests/test_bill.py`, `tests/fixtures/billing/access.jsonl`

**Interfaces:**
- Consumes: `Prices`, `CostResult`(Task 5).
- Produces:
```python
class Usage(BaseModel):
    tenant: str; model: str; input_tokens: int; cached_input_tokens: int; output_tokens: int; requests: int
def read_log(lines: Iterable[str]) -> Iterator[dict]: ...         # skips non-JSON lines with a count
def aggregate(records: Iterable[dict], start: datetime, end: datetime) -> list[Usage]: ...  # [start, end)
def invoice_rows(usage: list[Usage], prices: Prices, discounts: dict[str, float]) -> list[dict]: ...
def write_invoice(rows, path: Path) -> None: ...
def write_cloudevents(records, start, end, path: Path) -> None: ...
def prune(log_dir: Path, retention_days: int, now: datetime) -> list[Path]: ...
```

- [ ] **Step 1: 테스트**

```python
import csv
import json
from datetime import datetime, timezone

from ttalkkak.billing.bill import aggregate, invoice_rows, read_log, write_cloudevents
from ttalkkak.cost.model import Prices

UTC = timezone.utc
L = [
    {"ts": "2026-11-01T00:00:00Z", "request_id": "r1", "tenant": "acme", "subject": "u1", "model": "m", "status": 200, "input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10, "duration_ms": 5},
    {"ts": "2026-11-01T00:00:00Z", "request_id": "r1", "tenant": "acme", "subject": "u1", "model": "m", "status": 200, "input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10, "duration_ms": 5},
    {"ts": "2026-11-15T00:00:00Z", "request_id": "r2", "tenant": "acme", "subject": "u1", "model": "m", "status": 503, "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 0, "duration_ms": 5},
    {"ts": "2026-11-30T23:59:59Z", "request_id": "r3", "tenant": "beta", "subject": "k", "model": "m", "status": 200, "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 2, "duration_ms": 5},
    {"ts": "2026-12-01T00:00:00Z", "request_id": "r4", "tenant": "beta", "subject": "k", "model": "m", "status": 200, "input_tokens": 9, "cached_input_tokens": 0, "output_tokens": 9, "duration_ms": 5},
]
NOV = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


def recs():
    return list(read_log([json.dumps(x) for x in L] + ["not json"]))


def test_bill_dedup_and_period_bounds():
    u = {x.tenant: x for x in aggregate(recs(), *NOV)}
    assert u["acme"].requests == 1 and u["acme"].input_tokens == 100
    assert u["beta"].output_tokens == 2  # r4 is December


def test_failed_requests_are_not_billed():
    u = {x.tenant: x for x in aggregate(recs(), *NOV)}
    assert u["acme"].output_tokens == 10


def test_invoice_amounts_and_discount():
    rows = invoice_rows(aggregate(recs(), *NOV), Prices(input=1_000_000, cached_input=100_000, output=2_000_000), {"acme": 0.5})
    acme = next(r for r in rows if r["tenant"] == "acme")
    # (100-50)*1 + 50*0.1 + 10*2 = 75 KRW, half off
    assert acme["amount_krw"] == 37.5


def test_cloudevents_shape(tmp_path):
    p = tmp_path / "e.jsonl"
    write_cloudevents(recs(), *NOV, p)
    e = json.loads(p.read_text().splitlines()[0])
    assert e["specversion"] == "1.0" and e["type"] == "ttalkkak.tokens" and e["subject"] == "acme"
    assert "prompt" not in json.dumps(e) and "content" not in json.dumps(e)
```
(입력 토큰 금액은 캐시되지 않은 입력 `input − cached` × 입력가 + `cached` × 캐시가.)

- [ ] **Step 2: 구현과 `ops bill` 명령**

`ops bill --logs <파일|디렉터리> --from YYYY-MM-DD --to YYYY-MM-DD [--cost runs/<run>/cost.json] [--discounts bill.yaml] [--out .] [--prune --retention-days 120] [--from-cluster]`. `--from-cluster`는 `kubectl logs -n ttalkkak -l app=envoy --since-time`로 읽는다(Task 11의 kubectl 래퍼 재사용). 건너뛴 줄 수를 출력.

- [ ] **Step 3: 테스트와 Commit**

```bash
git add -A && git commit -m "Add ttalkkak ops bill: dedup, period bounds, invoice CSV and CloudEvents"
```

---

### Task 9: `ops serve` (가격표·사용량·인증 설정 API, 테넌트 지표)

**Files:**
- Create: `src/ttalkkak/ops_api/__init__.py`, `src/ttalkkak/ops_api/app.py`, `tests/test_ops_api.py`

**Interfaces:**
- Consumes: `TenantStore`, `CostResult`, `aggregate`(Task 8).
- Produces: `create_ops_api(store, cost, log_source, auth_cfg) -> Starlette`
  - `GET /v1/pricing` → `{"currency": "KRW", "per": 1_000_000, "input": .., "cached_input": .., "output": .., "note": "estimate"}`
  - `GET /v1/usage` → 헤더 `x-ttalkkak-tenant`(게이트웨이가 넣음)로 테넌트를 정해 오늘·이번 달 토큰과 예상 금액. 헤더가 없거나 콤마가 있으면 400.
  - `GET /v1/auth-config` → `{"issuer": ..., "client_id": "ttalkkak-cli"}`(인증 없이)
  - `GET /metrics` → `ttalkkak_tokens_total{tenant,model,kind}`, `ttalkkak_requests_total{tenant,status}`(로그를 주기적으로 읽어 누적)
  - `ops serve --port 8790`로 띄우고, 매니페스트(Task 6 `40-gateway.yaml`)에서 `/v1/pricing|usage|auth-config`를 이 서비스로 라우팅하도록 `20-vllm.yaml` 옆에 `25-ops-serve.yaml`(Deployment·Service)를 추가한다. 이미지는 `python:3.12-slim` + `pip install ttalkkak[ops]`(로컬 wheel을 ConfigMap 대신 이미지로 빌드하는 방법을 README에, 데스크탑에서는 k3s `ctr images import`).

- [ ] **Step 1: 테스트**

```python
from starlette.testclient import TestClient

from ttalkkak.ops_api.app import create_ops_api


def client(tmp_path, lines):
    ...  # build store with acme, cost with known prices, log_source returning `lines`
    return TestClient(create_ops_api(...))


def test_pricing_is_public_estimate(tmp_path):
    r = client(tmp_path, []).get("/v1/pricing")
    assert r.status_code == 200 and r.json()["note"] == "estimate"


def test_usage_needs_single_tenant_header(tmp_path):
    c = client(tmp_path, [])
    assert c.get("/v1/usage").status_code == 400
    assert c.get("/v1/usage", headers={"x-ttalkkak-tenant": "a,b"}).status_code == 400


def test_usage_rejects_multi_tenant_token(tmp_path):
    assert client(tmp_path, []).get("/v1/usage", headers={"x-ttalkkak-tenant": "acme,beta"}).status_code == 400


def test_usage_sums_today_and_month(tmp_path):
    ...  # two log lines for acme today, one earlier this month; assert totals and amount


def test_metrics_have_tenant_labels(tmp_path):
    ...  # assert 'ttalkkak_tokens_total{tenant="acme",model="m",kind="output"}' in text
```
(`...` 부분은 Task 8 테스트의 로그 형식과 Task 5의 `Prices`로 구체적인 값을 넣어 작성한다. 기대값은 그 값으로 손계산.)

- [ ] **Step 2: 구현, `ops serve` 명령, Commit**

```bash
git add -A && git commit -m "Add ttalkkak ops serve: pricing, usage, auth config and per-tenant metrics"
```

---

### Task 10: 최소 콘솔 대화 (`ttalkkak` 인자 없음)

**Files:**
- Modify: `src/ttalkkak/client/console.py`
- Create: `tests/test_console.py`

**Interfaces:**
- Consumes: `load_config`, `valid_access_token`, `tenant_of`(Task 3), `/v1/pricing`(Task 9).
- Produces: `main(argv=None, http=None, input_fn=input, out=sys.stdout) -> int`. 6단계에서 에이전트로 교체될 진입점.

동작: 설정이 없으면 `login` 흐름을 바로 시작(처음 실행 경험). 로그인돼 있으면 `/v1/models`에서 모델을 고르고(설정에 있으면 그대로), 입력 → `/v1/chat/completions`(stream, `stream_options.include_usage=true`) → 토큰을 흘려 출력 → 턴 끝에 `입력 120 (캐시 64) · 출력 45 · 약 0.03원`. `/exit`, Ctrl-D로 종료. 401이면 갱신 후 한 번 재시도, 그래도 401이면 "다시 로그인하세요".

- [ ] **Step 1: 테스트** (`httpx.MockTransport`로 스트리밍 SSE와 usage 청크를 흉내)
```python
def test_streams_reply_and_prints_usage_and_estimate(...): ...
def test_refreshes_once_on_401_then_asks_to_login(...): ...
def test_first_run_without_config_starts_login(...): ...
```
각 테스트는 SSE 본문 문자열(`data: {...}\n\n` … `data: [DONE]`)을 직접 만들어 넣고, 출력 문자열을 비교한다.

- [ ] **Step 2: 구현과 Commit**

```bash
git add -A && git commit -m "Run a minimal streaming console chat when ttalkkak is called without arguments"
```

---

### Task 11: 원가 측정 실행기와 `ops cost`, `ops cluster up/down/status`

**Files:**
- Create: `src/ttalkkak/cost/runner.py`, `src/ttalkkak/cluster/__init__.py`, `src/ttalkkak/cluster/apply.py`, `tests/test_cost_runner.py`, `tests/test_cluster.py`, `tests/fixtures/guidellm/concurrent_mock.json`

**Interfaces:**
- Consumes: `POWER_CMD`, `parse_nvsmi_csv`, `energy_j`(Task 4), `compute`, `load_or_create_config`(Task 5), 기존 `resolve_endpoint`.
- Produces:
```python
def guidellm_cmd(target: str, model: str, data: str, out: Path, streams: list[int] | None, max_seconds: int) -> list[str]: ...
def parse_points(report: dict) -> list[Point]: ...          # one per benchmark (concurrent profile)
def parse_energy_run(report: dict, samples: list[Sample]) -> EnergyRun: ...  # whole report window
def run_cost(run_dir: Path, cfg_path: Path, run=subprocess.run, popen=subprocess.Popen) -> CostResult: ...
# cluster/apply.py
def apply_dir(out: Path, kubectl=run_kubectl, wait_s: int = 300) -> list[str]: ...
def status(ns: str, kubectl=run_kubectl) -> list[dict]: ...
def down(ns: str, kubectl=run_kubectl) -> None: ...
```
- guidellm 명령(0.8.1, `guidellm run --help`에서 확인한 형식):
  - A: `--profile kind=concurrent --override profile.streams 1,2,4,8,16 --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 --constraint kind=max_duration,seconds=60`
  - B: `--profile kind=concurrent --override profile.streams 8 --data kind=synthetic_text,prompt_tokens=2048,output_tokens=1 --constraint kind=max_duration,seconds=60`
  - C: 같은 1792 토큰 앞부분을 공유하는 데이터(guidellm의 공유 prefix 옵션을 `--help`에서 확인해 쓰고, 없으면 JSONL 데이터 파일을 만들어 `--data kind=file`)
- `parse_points`는 각 benchmark의 `metrics.output_tokens_per_second.successful.mean`, `time_to_first_token_ms.successful.percentiles.p99`, `inter_token_latency_ms.successful.percentiles.p99`, streams는 `config`에서. `parse_energy_run`은 `prompt_token_count.successful.total_sum`, `cached_token_count.successful.total_sum`, `output_token_count.successful.total_sum`과 각 benchmark `[start_time, end_time]` 구간 에너지의 합.

- [ ] **Step 1: guidellm 결과 픽스처**

`tests/fixtures/guidellm/report_mock.json`(기존)을 복사해 benchmarks를 3개(streams 1, 2, 4)로 늘린 `concurrent_mock.json`을 만든다. 각 benchmark의 시각과 토큰 합을 서로 다르게 둔다.

- [ ] **Step 2: 실패하는 테스트**

```python
def test_parse_points_from_concurrent_report(): ...      # 3 points, streams 1,2,4, p99 read from percentiles
def test_energy_run_sums_benchmark_windows(): ...         # fake samples at 100 W constant -> 100 * total seconds
def test_run_cost_writes_json_and_markdown(tmp_path): ... # fake run/popen produce the three reports and a CSV
def test_run_cost_creates_default_config_and_says_so(tmp_path): ...
```
`run_cost`의 가짜 `popen`은 `stdout`으로 `tests/fixtures/power/nvsmi.csv` 형식의 줄을 시각만 바꿔 돌려준다.

- [ ] **Step 3: 구현과 `ops cost` 명령**

`ops cost --run <run> [--config cost.yaml] [--target-output-tok-s N]`. 엔진 주소는 `resolve_endpoint(run_dir)`. 끝나면 `cost.md` 내용을 출력. 기본 설정을 새로 만들었으면 "cost.yaml 기본값으로 계산했습니다. 값을 고친 뒤 다시 실행하세요"를 먼저 출력.

- [ ] **Step 4: `cluster` 테스트와 구현**

```python
def test_apply_in_file_order_and_wait(tmp_path): ...     # fake kubectl records calls; 00,10,15,20,25,30,40,50 order; waits after each
def test_apply_stops_with_describe_on_timeout(tmp_path): ...
def test_status_table_rows(): ...                          # parses fake `kubectl get pods -o json`
def test_down_requires_yes(): ...
```
`ops cluster up --out <dir> [--wait 300]`, `status`(구성요소, 준비, 재시작, 주소; 마지막 줄에 `/v1/pricing` 시험 요청 결과), `down [--yes]`. `kubectl`이 없으면 "kubectl이 필요합니다".

- [ ] **Step 5: 테스트와 Commit**

```bash
git add -A && git commit -m "Add ttalkkak ops cost and ops cluster up/down/status"
```

---

### Task 11b: 운영 화면 탭 (`ops ui`)

**Files:**
- Modify: `src/ttalkkak/ui/app.py`, `src/ttalkkak/ui/static/index.html`, `tests/test_ui_app.py`

**Interfaces:**
- Consumes: `TenantStore`(Task 2), `CostResult`(Task 5), `aggregate`(Task 8), `status`(Task 11).
- Produces: 새 API(로컬 전용, 기존 보안 미들웨어 그대로): `GET /api/ops/status`, `GET /api/ops/cost?run=`, `GET/POST /api/ops/tenants`, `POST /api/ops/tenants/{id}/keys`(발급, 응답에 원문 한 번), `POST /api/ops/keys/{prefix}/revoke`, `POST /api/ops/tenants/{id}/limit|cache|rotate-salt`, `GET /api/ops/usage?from=&to=`, `GET /api/ops/invoice.csv?from=&to=`.

화면: 상단 바 아래 탭 `서빙 실행`(기존) · `개요` · `원가` · `테넌트` · `사용량`. 스타일은 `fe9990c`의 콘솔 스타일을 그대로 쓴다. 키 발급 결과는 모달에 한 번만 보여 주고 "복사" 버튼, 닫으면 다시 볼 수 없다는 문구.

- [ ] **Step 1: API 테스트(각 경로 1개씩, 키 원문이 목록 응답에 없는지 포함)**
- [ ] **Step 2: 구현**
- [ ] **Step 3: 맥 브라우저로 탭 4개를 눌러 보고 스크린샷을 `docs/screenshots/stage5-*.png`로 저장**(클러스터 없이 보이는 빈 상태·오류 상태 포함)
- [ ] **Step 4: Commit**
```bash
git add -A && git commit -m "Add operator tabs to the UI: overview, cost, tenants, usage"
```

---

### Task 12: 데스크탑 단계 0 — 실제 구성요소와 지표 이름 확인

**데스크탑(WSL2, k3s)에서 한다. hxccl B가 끝난 뒤.**

**Files:**
- Modify: 스펙 "환경 기록", `src/ttalkkak/gateway/versions.py`, `tests/test_observability.py`(METRICS 집합), `docs/stage5-ledger.md`

- [ ] **Step 1: k3s와 GPU 확인** — `kubectl get nodes`, NVIDIA device plugin 동작(기존 vllm-k8s 실습 환경), `kubectl describe node | grep nvidia.com/gpu`.
- [ ] **Step 2: Envoy Gateway, Envoy AI Gateway, Inference Extension CRD를 Task 6의 버전으로 설치**하고 `kubectl get crd | grep -E "aigateway|inference|gateway"`.
- [ ] **Step 3: DCGM exporter가 WSL에서 동작하는지**: DaemonSet을 띄우고 `/metrics`에 `DCGM_FI_DEV_POWER_USAGE`가 나오는지. 안 되면 nvidia-smi를 읽는 작은 exporter(`ttalkkak ops serve`에 `/metrics/gpu`로 추가)로 대체하고 LEDGER에 기록, 대시보드 지표 이름을 바꾼다.
- [ ] **Step 4: vLLM 실제 지표 이름**: 작은 모델로 vLLM 파드를 띄우고 `curl :8000/metrics | grep -E "^vllm:" | cut -d'{' -f1 | sort -u`. 결과로 `tests/test_observability.py`의 METRICS와 대시보드 쿼리를 맞춘다. 캐시 토큰을 usage에 넣는 옵션이 실제로 `prompt_tokens_details.cached_tokens`를 채우는지 요청 한 번으로 확인.
- [ ] **Step 5: 메모리 사용량**: 전 구성요소를 띄운 뒤 `kubectl top pods -A`, `free -m`. 7 GB를 넘으면 스펙 10절의 대응 중 하나를 고르고 LEDGER에.
- [ ] **Step 6: Commit과 push**
```bash
git add -A && git commit -m "Record the desktop environment and real metric names for stage 5"
```

---

### Task 13: 데스크탑 처음부터 끝까지 (스펙 10절 단계 1~7)

| 단계 | 명령 | 확인 |
|---|---|---|
| 1 | `ttalkkak ops up --model Qwen/Qwen2.5-1.5B-Instruct` → `ttalkkak ops cost --run <run>` | `cost.md`에 입력·캐시·출력 원가와 운영점, 전력 로그 |
| 2 | `ttalkkak ops tenants add acme/beta`, `key-issue`, `user-add acme u1@acme`, `ops export gateway --run <run> --out deploy/`, `ops cluster up --out deploy/`, `ops cluster status` | 전부 준비, API 키로 `curl /v1/chat/completions` 성공 |
| 2b | 다른 터미널에서 `ttalkkak login` → 브라우저 로그인 → `ttalkkak` 대화 | 턴별 토큰·예상 금액 표시, `logout` 뒤 401 |
| 3 | `set-limit acme 200` 후 연속 요청 | 429 |
| 4 | 같은 긴 프롬프트를 acme·beta 키로 각각 두 번 | acme 두 번째는 `cached_tokens > 0`, beta 첫 번째는 0 (vLLM 지표로도 확인) |
| 5 | `ttalkkak ops bill --from-cluster --from <오늘> --to <내일>` | 청구서 값이 손계산과 일치 |
| 6 | Grafana·Jaeger 접속 | 대시보드 3개에 실시간 값, 경보 규칙 로드, Jaeger에 게이트웨이→vLLM 구간 |
| 7 | `ttalkkak ops ui` | 운영 탭 4개, 스크린샷 |

각 단계 결과(명령 출력, 스크린샷)를 `docs/results/stage5/`에 저장하고, 실패는 원인을 측정한 만큼만 적는다.

- [ ] **Step: 결과 정리, README 갱신(설치·운영 흐름·제약), 브랜치 전체 리뷰, push**
```bash
git add -A && git commit -m "Validate stage 5 end to end on the desktop" && git push -u origin stage5
```
