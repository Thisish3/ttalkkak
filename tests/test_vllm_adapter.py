from pathlib import Path

import httpx

from ttalkkak.engines.vllm import VllmAdapter
from ttalkkak.schemas import Candidate

FIX = Path(__file__).parent / "fixtures/vllm"
A = VllmAdapter()


def test_build_args_maps_flags():
    c = Candidate(
        id="c1",
        engine="vllm",
        model_id="m/x",
        args={
            "max_model_len": 16384,
            "kv_cache_dtype": "fp8",
            "enable_auto_tool_choice": True,
            "tool_call_parser": "hermes",
            "gpu_memory_utilization": 0.9,
        },
    )
    spec = A.build_args(c)
    assert spec.args[:2] == ["--model", "m/x"]
    assert "--enable-auto-tool-choice" in spec.args
    i = spec.args.index("--tool-call-parser")
    assert spec.args[i + 1] == "hermes"
    assert "--max-model-len" in spec.args and "16384" in spec.args


def test_false_bool_flag_is_omitted():
    c = Candidate(
        id="c", engine="vllm", model_id="m", args={"enable_auto_tool_choice": False}
    )
    assert "--enable-auto-tool-choice" not in A.build_args(c).args


def test_read_applied_from_logs_metrics_and_models():
    a = A.read_applied(
        (FIX / "startup_sample.log").read_text(),
        {
            "metrics": (FIX / "metrics_sample.txt").read_text(),
            "models": {"data": [{"id": "m", "max_model_len": 16384}]},
        },
    )
    v = a.values
    assert v["max_model_len"].value == 16384
    assert v["kv_cache_dtype"].value == "fp8"
    assert v["kv_cache_tokens"].value == 71632
    assert v["attention_backend"].value == "FLASH_ATTN"
    assert v["tool_call_parser"].value == "hermes"
    assert "cache_config_info" in v["kv_cache_dtype"].evidence


def test_missing_values_are_unknown():
    a = A.read_applied("", {})
    assert a.values["attention_backend"].value is None
    assert a.values["attention_backend"].evidence == "unknown"


def test_collect_gets_models_and_metrics_and_skips_failures():
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(500)

    got = A.collect("http://x", httpx.Client(transport=httpx.MockTransport(handler)))
    assert got == {"models": {"data": []}}
    assert A.container_port == 8000


def test_health():
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert A.health("http://x", ok) and not A.health("http://x", bad)
