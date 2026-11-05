import json
from pathlib import Path

import httpx

from ttalkkak.engines.sglang import SglangAdapter
from ttalkkak.schemas import Candidate
from ttalkkak.verify.applied import compare_applied

FIX = Path(__file__).parent / "fixtures/sglang"
A = SglangAdapter()
C = Candidate(
    id="c3",
    engine="sglang",
    model_id="Qwen/Qwen2.5-1.5B-Instruct",
    args={
        "max_model_len": 16384,
        "kv_cache_dtype": "fp8",
        "gpu_memory_utilization": 0.9,
        "tool_call_parser": "qwen25",
    },
)


def test_build_args_translates_neutral_names():
    a = A.build_args(C).args
    assert a[:3] == ["python3", "-m", "sglang.launch_server"]
    assert a[3:5] == ["--model-path", "Qwen/Qwen2.5-1.5B-Instruct"]
    assert a[a.index("--context-length") + 1] == "16384"
    assert a[a.index("--kv-cache-dtype") + 1] == "fp8_e4m3"
    assert a[a.index("--mem-fraction-static") + 1] == "0.9"
    assert a[a.index("--tool-call-parser") + 1] == "qwen25"
    assert "--enable-metrics" in a and a[a.index("--port") + 1] == "30000"
    assert "--enable-auto-tool-choice" not in a


def test_read_applied_from_server_info_normalizes_fp8():
    info = json.loads((FIX / "server_info_sample.json").read_text())
    v = A.read_applied("", {"server_info": info}).values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_dtype"].value == "fp8"
    assert v["kv_cache_tokens"].value == 90112
    assert v["tool_call_parser"].value == "qwen25"
    assert "server_info" in v["kv_cache_dtype"].evidence
    assert compare_applied(C, A.read_applied("", {"server_info": info})) == []


def test_falls_back_to_log_without_server_info():
    log = (FIX / "startup_sample.log").read_text()
    v = A.read_applied(log, {}).values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_tokens"].value == 90112
    assert v["kv_cache_dtype"].value is None


def test_collect_tolerates_missing_server_info():
    def handler(req):
        if req.url.path == "/server_info":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={})

    got = A.collect("http://x", httpx.Client(transport=httpx.MockTransport(handler)))
    assert "server_info" not in got
