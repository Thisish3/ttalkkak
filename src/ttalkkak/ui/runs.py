import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ttalkkak.errors import TtalkkakError

_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class Busy(Exception):
    """A run is already in progress (one GPU, one run at a time)."""


class RunManager:
    def __init__(self, runs_root: Path, run_fn: Callable[[str, list[str], str], None]):
        self.runs_root = runs_root
        self.run_fn = run_fn
        self._lock = threading.Lock()
        self._running: str | None = None
        self._errors: dict[str, dict] = {}

    def safe_dir(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id or "") or run_id in (".", ".."):
            raise KeyError(run_id)
        return self.runs_root / run_id

    def start(self, model: str, skills: list[str]) -> str:
        with self._lock:
            if self._running:
                raise Busy(self._running)
            run_id = time.strftime("%Y%m%d-%H%M%S")
            while (self.runs_root / run_id).exists():
                run_id += "x"
            self._running = run_id
        threading.Thread(
            target=self._work, args=(model, skills, run_id), daemon=True
        ).start()
        return run_id

    def _work(self, model: str, skills: list[str], run_id: str) -> None:
        try:
            self.run_fn(model, skills, run_id)
        except TtalkkakError as err:
            self._errors[run_id] = {"message": str(err), "exit_code": err.exit_code}
        except Exception as err:  # noqa: BLE001 - the UI must keep serving
            self._errors[run_id] = {
                "message": f"{type(err).__name__}: {err}",
                "exit_code": 1,
            }
        finally:
            with self._lock:
                self._running = None

    def status(self, run_id: str) -> dict:
        d = self.safe_dir(run_id)
        running = self._running == run_id
        if not d.is_dir() and not running:
            raise KeyError(run_id)

        def load(name):
            p = d / name
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

        state = load("state.json") or {}
        report = d / "report.md"
        return {
            "run_id": run_id,
            "running": running,
            "done": state.get("done", []),
            "error": self._errors.get(run_id),
            "pick": load("pick.json"),
            "report_md": report.read_text(encoding="utf-8")
            if report.is_file()
            else None,
        }

    def list(self) -> list[dict]:
        if not self.runs_root.is_dir():
            return []
        dirs = sorted(
            (p for p in self.runs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        out = []
        for p in dirs:
            if _RUN_ID.match(p.name):
                st = self.status(p.name)
                out.append(
                    {k: st[k] for k in ("run_id", "running", "done", "error")}
                    | {"chosen": (st["pick"] or {}).get("chosen")}
                )
        return out
