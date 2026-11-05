import json
import shutil
from pathlib import Path

from ttalkkak.verify.accuracy import accuracy_cmd, parse_accuracy
from ttalkkak.verify.perf import parse_perf, perf_cmd

FIX = Path(__file__).parent / "fixtures"


def test_accuracy_cmd_targets_chat_completions(tmp_path):
    cmd = accuracy_cmd("http://h:8001/v1", "m", tmp_path, limit=100)
    joined = " ".join(cmd)
    assert "local-chat-completions" in joined and "--limit 100" in joined
    assert "base_url=http://h:8001/v1/chat/completions" in joined
    assert "--apply_chat_template" in cmd


def test_parse_accuracy_reads_nested_results_file(tmp_path):
    d = tmp_path / "m__x"
    d.mkdir()
    shutil.copy(FIX / "lm_eval/results_sample.json", d / "results_2026-11-05.json")
    assert parse_accuracy(tmp_path) == 0.64


def test_perf_cmd_shape(tmp_path):
    cmd = perf_cmd("http://h:8001/v1", "m", tmp_path / "p.json", requests=20)
    assert cmd[:2] == ["guidellm", "run"]
    assert "kind=openai_http,target=http://h:8001,model=m" in cmd
    assert "kind=max_requests,count=20" in cmd


def test_parse_perf_from_real_guidellm_report():
    m = parse_perf(json.loads((FIX / "guidellm/report_mock.json").read_text()))
    assert round(m["output_tok_s"], 1) == 17.7 and round(m["ttft_ms"]) == 106
