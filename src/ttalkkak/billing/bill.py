import csv
import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from ttalkkak.cost.model import MTOK, Prices
from ttalkkak.tenants.store import TENANT_ID

TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


class Usage(BaseModel):
    tenant: str
    model: str
    input_tokens: int = 0  # includes cached_input_tokens
    cached_input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0


def _int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def read_log(lines: Iterable[str], stats: dict | None = None) -> Iterator[dict]:
    """Parse gateway access-log JSON lines; Envoy writes '-' for missing values."""
    skipped = 0
    for line in lines:
        try:
            r = json.loads(line)
            ts = datetime.fromisoformat(r["ts"])
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        if not isinstance(r, dict):
            skipped += 1
            continue
        yield {
            "ts": ts,
            "request_id": str(r.get("request_id") or ""),
            "tenant": str(r.get("tenant") or ""),
            "model": str(r.get("model") or "-"),
            "status": _int(r.get("status")),
            **{k: _int(r.get(k)) for k in TOKEN_FIELDS},
        }
    if stats is not None:
        stats["skipped"] = stats.get("skipped", 0) + skipped


def billable(records: Iterable[dict], start: datetime, end: datetime) -> Iterator[dict]:
    """Records in [start, end), deduplicated by request_id, without 5xx or no tenant."""
    seen = set()
    for r in records:
        if not start <= r["ts"] < end or r["status"] >= 500:
            continue
        if not TENANT_ID.match(r["tenant"]):
            continue
        if r["request_id"]:
            if r["request_id"] in seen:
                continue
            seen.add(r["request_id"])
        yield r


def aggregate(records: Iterable[dict], start: datetime, end: datetime) -> list[Usage]:
    out: dict[tuple[str, str], Usage] = {}
    for r in billable(records, start, end):
        u = out.setdefault(
            (r["tenant"], r["model"]), Usage(tenant=r["tenant"], model=r["model"])
        )
        u.requests += 1
        for k in TOKEN_FIELDS:
            setattr(u, k, getattr(u, k) + r[k])
    return sorted(out.values(), key=lambda u: (u.tenant, u.model))


def invoice_rows(
    usage: list[Usage], prices: Prices, discounts: dict[str, float]
) -> list[dict]:
    rows = []
    for u in usage:
        fresh = max(0, u.input_tokens - u.cached_input_tokens)
        gross = (
            fresh * prices.input
            + u.cached_input_tokens * prices.cached_input
            + u.output_tokens * prices.output
        ) / MTOK
        discount = discounts.get(u.tenant, 0.0)
        rows.append(
            {
                **u.model_dump(),
                "price_input_krw_per_mtok": prices.input,
                "price_cached_input_krw_per_mtok": prices.cached_input,
                "price_output_krw_per_mtok": prices.output,
                "discount": discount,
                "amount_krw": round(gross * (1 - discount), 2),
            }
        )
    return rows


def write_invoice(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["tenant", "amount_krw"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_cloudevents(
    records: Iterable[dict], start: datetime, end: datetime, path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in billable(records, start, end):
            event = {
                "specversion": "1.0",
                "type": "ttalkkak.tokens",
                "source": "ttalkkak/gateway",
                "id": r["request_id"],
                "time": r["ts"].isoformat(),
                "subject": r["tenant"],
                "data": {"model": r["model"], **{k: r[k] for k in TOKEN_FIELDS}},
            }
            f.write(json.dumps(event) + "\n")


def prune(log_dir: Path, retention_days: int, now: datetime) -> list[Path]:
    cutoff = (now - timedelta(days=retention_days)).timestamp()
    old = [p for p in sorted(log_dir.glob("*.jsonl")) if p.stat().st_mtime < cutoff]
    for p in old:
        p.unlink()
    return old
