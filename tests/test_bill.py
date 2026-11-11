import csv
import json
import os
import time
from datetime import UTC, datetime

from typer.testing import CliRunner

from ttalkkak.billing.bill import (
    aggregate,
    invoice_rows,
    prune,
    read_log,
    write_cloudevents,
)
from ttalkkak.cli import app
from ttalkkak.cost.model import Prices


def rec(ts, rid, tenant, status=200, i=100, c=50, o=10, **kw):
    return {
        "ts": ts, "request_id": rid, "tenant": tenant, "subject": "u1", "model": "m",
        "status": status, "input_tokens": i, "cached_input_tokens": c,
        "output_tokens": o, "duration_ms": 5, **kw,
    }  # fmt: skip


L = [
    rec("2026-11-01T00:00:00.000Z", "r1", "acme"),
    rec("2026-11-01T00:00:00.000Z", "r1", "acme"),
    rec("2026-11-15T00:00:00.000Z", "r2", "acme", status=503, c=0, o=0),
    rec("2026-11-30T23:59:59.999Z", "r3", "beta", i=1, c=0, o=2),
    rec("2026-12-01T00:00:00.000Z", "r4", "beta", i=9, c=0, o=9),
    rec("2026-11-02T00:00:00.000Z", "r5", "-", i=7, c=0, o=7),
]
NOV = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
PRICES = Prices(input=1_000_000, cached_input=100_000, output=2_000_000)


def lines():
    envoy_style = json.dumps(
        rec("2026-11-03T00:00:00.000Z", "r6", "beta", i="-", c="-", o="3")
    )
    return [json.dumps(x) for x in L] + ["not json", envoy_style]


def test_bill_dedup_and_period_bounds():
    u = {x.tenant: x for x in aggregate(read_log(lines()), *NOV)}
    assert u["acme"].requests == 1 and u["acme"].input_tokens == 100
    assert u["beta"].output_tokens == 2 + 3  # r4 is December
    assert "-" not in u


def test_failed_requests_are_not_billed():
    u = {x.tenant: x for x in aggregate(read_log(lines()), *NOV)}
    assert u["acme"].output_tokens == 10 and u["acme"].requests == 1


def test_read_log_counts_skipped_lines():
    stats = {}
    list(read_log(lines(), stats))
    assert stats["skipped"] == 1


def test_invoice_amounts_and_discount():
    rows = invoice_rows(aggregate(read_log(lines()), *NOV), PRICES, {"acme": 0.5})
    acme = next(r for r in rows if r["tenant"] == "acme")
    # (100-50)*1 + 50*0.1 + 10*2 = 75 KRW, half off
    assert acme["amount_krw"] == 37.5


def test_cloudevents_shape(tmp_path):
    p = tmp_path / "e.jsonl"
    write_cloudevents(read_log(lines()), *NOV, p)
    events = [json.loads(x) for x in p.read_text().splitlines()]
    assert {e["id"] for e in events} == {"r1", "r3", "r6"}
    e = events[0]
    assert e["specversion"] == "1.0" and e["type"] == "ttalkkak.tokens"
    assert e["subject"] == "acme" and e["data"]["output_tokens"] == 10
    assert "prompt" not in json.dumps(events) and "content" not in json.dumps(events)


def test_prune_removes_only_old_files(tmp_path):
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old.write_text("")
    new.write_text("")
    t = time.time() - 121 * 86400
    os.utime(old, (t, t))
    assert prune(tmp_path, 120, datetime.now(UTC)) == [old]
    assert not old.exists() and new.exists()


def test_cli_bill_writes_invoice_and_events(tmp_path):
    logs = tmp_path / "access.jsonl"
    logs.write_text("\n".join(lines()))
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps({"price_krw_per_mtok": PRICES.model_dump()}))
    r = CliRunner().invoke(
        app,
        ["ops", "bill", "--logs", str(logs), "--from", "2026-11-01", "--to",
         "2026-12-01", "--cost", str(cost), "--out", str(tmp_path)],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    inv = tmp_path / "invoice-2026-11-01_2026-12-01.csv"
    rows = list(csv.DictReader(inv.open()))
    assert {x["tenant"] for x in rows} == {"acme", "beta"}
    assert (tmp_path / "usage-2026-11-01_2026-12-01.cloudevents.jsonl").exists()
    assert "건너뛴 줄 1" in r.output


def test_cli_bill_from_cluster_uses_gateway_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.cluster.apply.gateway_logs", lambda: lines())
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps({"price_krw_per_mtok": PRICES.model_dump()}))
    r = CliRunner().invoke(
        app,
        ["ops", "bill", "--from-cluster", "--from", "2026-11-01", "--to", "2026-12-01",
         "--cost", str(cost), "--out", str(tmp_path)],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    rows = list(csv.DictReader((tmp_path / "invoice-2026-11-01_2026-12-01.csv").open()))
    assert {x["tenant"] for x in rows} == {"acme", "beta"}
