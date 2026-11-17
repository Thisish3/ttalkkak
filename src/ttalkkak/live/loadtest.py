"""Console load test: guidellm's goodput search, an open-loop check, capacity."""

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
    capacity_from_search,
    merge_capacity,
)
from ttalkkak.cost.runner import (
    goodput_cmd,
    parse_confirm,
    parse_goodput,
    poisson_cmd,
    throughput_knee,
)
from ttalkkak.errors import TtalkkakError

MAX_USERS = 100
TARGET_ATTAINMENT = 0.95
PROBE_SECONDS = 60
PROBE_REQUESTS = 150
MAX_PROBES = 12
# Open-loop check at 90 % of the request rate measured at the capacity level:
# Poisson arrivals are burstier than a closed loop, so the full rate would test
# a level the search never claimed.
CONFIRM_FACTOR = 0.9
CONFIRM_SECONDS = 60
DATA = "kind=synthetic_text,prompt_tokens=256,output_tokens=128"
TEST_ID = re.compile(r"^[0-9]{8}-[0-9]{6}(-[0-9]+)?$")
CAPACITY_CONFIGMAP = "ttalkkak-capacity"


def check_max_users(max_users: int) -> None:
    if isinstance(max_users, bool) or not 1 <= max_users <= MAX_USERS:
        raise ValueError(f"최대 사용자 수는 1~{MAX_USERS}이어야 합니다")


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
        itl_target_ms: float | None = None,
        target_attainment: float = TARGET_ATTAINMENT,
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
        self.itl_target_ms = itl_target_ms
        self.target_attainment = target_attainment
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
        check_max_users(max_users)
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
            "method": "goodput",
            "max_users": max_users,
            "ttft_target_ms": self.ttft_target_ms,
            "itl_target_ms": self.itl_target_ms,
            "target_attainment": self.target_attainment,
            "phase": "search",
            "stages": [],
            "stop_reason": None,
            "best_passing_streams": None,
            "lowest_failing_streams": None,
            "capacity_streams": None,
            "capacity_tpm": None,
            "no_capacity_reason": None,
            "confirm": None,
            "knee": None,
            "error": None,
        }
        try:
            self._save(record)
            args = (record, created)
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

    def _work(self, record: dict, created: datetime) -> None:
        prefix = None
        try:
            key = self._issue_key()
            prefix = key[: len(f"tk_{LOADTEST_TENANT}_") + 8]
            probes, search = parse_goodput(
                self._guidellm(record["id"], "search", key, record["max_users"])
            )
            record["stages"] = [p.model_dump() for p in probes]
            record.update(search)
            record["knee"] = throughput_knee(probes)
            best, why = capacity_from_search(probes, search["stop_reason"])
            record["no_capacity_reason"] = why
            if best is not None:
                record["capacity_streams"] = best.users
                record["capacity_tpm"] = best.tokens_per_min
                record["phase"] = "confirm"
                self._save(record)
                rate = round(best.request_rate * CONFIRM_FACTOR, 3)
                report = self._guidellm(record["id"], "confirm", key, rate)
                record["confirm"] = {
                    **parse_confirm(report, self.target_attainment),
                    "rate_rps": rate,
                }
                if not record["autoscale_on"]:
                    self._write_capacity(record, created)
            record["phase"] = "done"
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

    def _guidellm(self, test_id: str, kind: str, key: str, arg) -> dict:
        """Run the search (arg = max users) or the open-loop check (arg = rate)."""
        work = self.dir / "stages"
        work.mkdir(parents=True, exist_ok=True)
        out = work / f"{test_id}-{kind}.guidellm.json"
        common = {
            "ttft_ms": self.ttft_target_ms,
            "itl_ms": self.itl_target_ms,
            "api_key": key,
        }
        if kind == "search":
            cmd = goodput_cmd(
                self.gateway_url, self.model, DATA, out, **common,
                attainment=self.target_attainment, max_streams=arg,
                probe_seconds=PROBE_SECONDS, probe_requests=PROBE_REQUESTS,
                max_probes=MAX_PROBES,
            )  # fmt: skip
            timeout = MAX_PROBES * (PROBE_SECONDS + 30) + 300
        else:
            cmd = poisson_cmd(
                self.gateway_url, self.model, DATA, out, **common,
                rate=arg, seconds=CONFIRM_SECONDS,
            )  # fmt: skip
            timeout = CONFIRM_SECONDS * 4 + 300
        try:
            self.run(cmd, check=True, timeout=timeout)
            return json.loads(out.read_text(encoding="utf-8"))
        finally:
            out.unlink(missing_ok=True)

    def _write_capacity(self, record: dict, created: datetime) -> None:
        if self.kubectl is None:
            raise TtalkkakError("kubectl이 없어 용량 기록을 저장하지 못했습니다")
        confirm, knee = record["confirm"], record["knee"] or {}
        cap = CapacityRecord(
            loadtest_id=record["id"],
            created_at=created,
            replicas=record["replicas"],
            model=record["model"],
            ttft_target_ms=self.ttft_target_ms,
            max_tokens_per_min=record["capacity_tpm"],
            method="goodput",
            target_attainment=self.target_attainment,
            itl_target_ms=self.itl_target_ms,
            stop_reason=record["stop_reason"],
            capacity_streams=record["capacity_streams"],
            confirmed=confirm["held"],
            confirm_rate_rps=confirm["rate_rps"],
            knee_streams=knee.get("streams"),
            knee_output_tok_s=knee.get("output_tok_s"),
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
