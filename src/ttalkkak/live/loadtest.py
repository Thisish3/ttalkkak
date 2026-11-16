"""Console load test: guidellm in staged concurrent users, capacity record."""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from ttalkkak.capacity import (
    LOADTEST_TENANT,
    CapacityRecord,
    Stage,
    capacity_from_stages,
    merge_capacity,
)
from ttalkkak.cost.runner import guidellm_cmd, parse_points, parse_stage_load
from ttalkkak.errors import TtalkkakError

STAGES = [1, 5, 10, 20]
STAGE_SECONDS = 15
MAX_USERS = 100
DATA = "kind=synthetic_text,prompt_tokens=256,output_tokens=128"
TEST_ID = re.compile(r"^[0-9]{8}-[0-9]{6}(-[0-9]+)?$")
CAPACITY_CONFIGMAP = "ttalkkak-capacity"


def stages_for(max_users: int) -> list[int]:
    if isinstance(max_users, bool) or not 1 <= max_users <= MAX_USERS:
        raise ValueError(f"최대 사용자 수는 1~{MAX_USERS}이어야 합니다")
    stages = [s for s in STAGES if s <= max_users]
    if max_users not in stages:
        stages.append(max_users)
    return stages


class LoadTestRunner:
    def __init__(
        self,
        root: Path,
        store,
        gateway_url: str,
        model: str,
        run=subprocess.run,
        now=lambda: datetime.now(UTC),
        kubectl=None,
        namespace: str = "ttalkkak",
        ttft_target_ms: float = 1000,
        sync: bool = False,
    ):
        self.dir = Path(root) / "runs" / "loadtests"
        self.store = store
        self.gateway_url = gateway_url
        self.model = model
        self.run = run
        self.now = now
        self.kubectl = kubectl
        self.namespace = namespace
        self.ttft_target_ms = ttft_target_ms
        self.sync = sync
        self._lock = threading.Lock()
        self._active: str | None = None

    @property
    def busy(self) -> bool:
        return self._active is not None

    def start(
        self,
        max_users: int,
        replicas: int,
        autoscale_on: bool,
        model: str | None = None,
    ) -> str:
        stages = stages_for(max_users)
        with self._lock:
            if self._active is not None:
                raise TtalkkakError("이미 부하 시험이 실행 중입니다", 409)
            created = self.now()
            base = created.strftime("%Y%m%d-%H%M%S")
            test_id, n = base, 1
            while (self.dir / f"{test_id}.json").exists():
                n += 1
                test_id = f"{base}-{n}"
            self._active = test_id
            if model:
                self.model = model
        record = {
            "id": test_id,
            "created_at": created.isoformat(),
            "replicas": replicas,
            "model": self.model,
            "autoscale_on": autoscale_on,
            "stages": [],
            "capacity_tpm": None,
            "error": None,
        }
        try:
            self._save(record)
            args = (record, stages, created)
            if self.sync:
                self._work(*args)
            else:
                threading.Thread(target=self._work, args=args, daemon=True).start()
        except BaseException:
            with self._lock:
                self._active = None
            raise
        return test_id

    def status(self, test_id: str) -> dict:
        p = self.dir / f"{test_id}.json"
        if not TEST_ID.fullmatch(test_id) or not p.is_file():
            raise TtalkkakError("부하 시험 기록이 없습니다", 404)
        return {
            **json.loads(p.read_text(encoding="utf-8")),
            "running": test_id == self._active,
        }

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in self.dir.glob("*.json"):
            try:
                rec = self.status(p.stem)
                rec["created_at"]
            except (TtalkkakError, ValueError, KeyError, OSError):
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    def _save(self, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{record['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)

    def _work(self, record: dict, stages: list[int], created: datetime) -> None:
        prefix = None
        try:
            key = self._issue_key()
            prefix = key[: len(f"tk_{LOADTEST_TENANT}_") + 8]
            for users in stages:
                record["stages"].append(self._stage(users, key, record["id"]))
                self._save(record)
            tpm = capacity_from_stages(
                [Stage(**s) for s in record["stages"]], self.ttft_target_ms
            )
            record["capacity_tpm"] = tpm
            if not record["autoscale_on"]:
                self._write_capacity(record, created, tpm)
        except subprocess.CalledProcessError as err:
            record["error"] = f"guidellm이 실패했습니다 (종료 코드 {err.returncode})"
        except subprocess.TimeoutExpired:
            record["error"] = "guidellm이 제한 시간 안에 끝나지 않았습니다"
        except TtalkkakError as err:
            record["error"] = str(err)
        except Exception as err:  # noqa: BLE001 - record, never lose the lock
            record["error"] = f"부하 시험이 실패했습니다: {type(err).__name__}"
        finally:
            try:
                if prefix:
                    self._revoke(prefix)
            except Exception:  # noqa: BLE001
                record["error"] = (
                    record["error"] or "부하 시험 키를 폐기하지 못했습니다"
                )
            try:
                self._save(record)
            finally:
                self._active = None

    def _issue_key(self) -> str:
        if LOADTEST_TENANT not in {t.id for t in self.store.list_tenants()}:
            self.store.add_tenant(LOADTEST_TENANT, "ttalkkak load test")
        return self.store.issue_key(LOADTEST_TENANT)

    def _revoke(self, prefix: str) -> None:
        self.store.revoke_key(prefix)

    def _stage(self, users: int, key: str, test_id: str) -> dict:
        stage_dir = self.dir / "stages"
        stage_dir.mkdir(parents=True, exist_ok=True)
        out = stage_dir / f"{test_id}-{users}.guidellm.json"
        try:
            return self._run_stage(users, key, out)
        finally:
            out.unlink(missing_ok=True)

    def _run_stage(self, users: int, key: str, out: Path) -> dict:
        self.run(
            guidellm_cmd(
                self.gateway_url, self.model, DATA, out, [users], STAGE_SECONDS, key
            ),
            check=True,
            timeout=STAGE_SECONDS * 4 + 300,
        )
        report = json.loads(out.read_text(encoding="utf-8"))
        (bench,) = report["benchmarks"]
        (point,) = parse_points({"benchmarks": [bench]})
        tpm, error_rate = parse_stage_load(bench)
        return Stage(
            users=users,
            ttft_p99_ms=point.ttft_p99_ms,
            itl_p99_ms=point.itl_p99_ms,
            output_tok_s=point.output_tok_s,
            tokens_per_min=tpm,
            error_rate=error_rate,
        ).model_dump()

    def _write_capacity(
        self, record: dict, created: datetime, tpm: float | None
    ) -> None:
        if self.kubectl is None:
            raise TtalkkakError("kubectl이 없어 용량 기록을 저장하지 못했습니다")
        cap = CapacityRecord(
            loadtest_id=record["id"],
            created_at=created,
            replicas=record["replicas"],
            model=record["model"],
            ttft_target_ms=self.ttft_target_ms,
            max_tokens_per_min=tpm,
        )
        got = self.kubectl(
            ["get", "configmap", CAPACITY_CONFIGMAP, "-n", self.namespace, "-o", "json"]
        )
        existing = None
        if got.returncode == 0 and got.stdout:
            existing = (json.loads(got.stdout).get("data") or {}).get("capacity.json")
        elif got.returncode != 0 and "NotFound" not in (got.stderr or ""):
            raise TtalkkakError("기존 용량 기록을 읽지 못해 저장하지 않았습니다")
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": CAPACITY_CONFIGMAP, "namespace": self.namespace},
            "data": {"capacity.json": merge_capacity(existing, cap, self.namespace)},
        }
        r = self.kubectl(["apply", "-f", "-"], input=json.dumps(manifest))
        if r.returncode != 0:
            raise TtalkkakError("용량 기록을 ConfigMap에 저장하지 못했습니다")
