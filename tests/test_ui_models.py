from types import SimpleNamespace

from ttalkkak.schemas import GpuInfo
from ttalkkak.ui.models import search_models, weight_bytes

GPU8 = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def test_weight_bytes_by_dtype():
    assert (
        weight_bytes({"BF16": 10, "I32": 2, "F8_E4M3": 4, "WEIRD": 1}) == 20 + 1 + 4 + 2
    )


def fake_list(models):
    def list_models(**kw):
        assert kw["search"] == "qwen" and kw["pipeline_tag"] == "text-generation"
        assert "safetensors" in kw["expand"]
        return iter(models)

    return list_models


def m(id_, params=None, downloads=1):
    st = (
        SimpleNamespace(parameters=params, total=sum(params.values()))
        if params
        else None
    )
    return SimpleNamespace(id=id_, downloads=downloads, safetensors=st)


def test_search_marks_fit_by_weights():
    rows = search_models(
        "qwen",
        10,
        GPU8,
        fake_list(
            [
                m("small", {"BF16": 1_543_714_304}),
                m("huge", {"BF16": 70_000_000_000}),
                m("gguf"),
            ]
        ),
    )
    by = {r["id"]: r for r in rows}
    assert by["small"]["fits"] is True and round(by["small"]["weight_gib"], 1) == 2.9
    assert by["huge"]["fits"] is False
    assert by["gguf"]["fits"] is None and by["gguf"]["weight_gib"] is None


def test_search_without_gpu_has_unknown_fit():
    rows = search_models("qwen", 10, None, fake_list([m("small", {"BF16": 10})]))
    assert rows[0]["fits"] is None


def test_awq_int4_packed_as_i32_counts_half_a_byte():
    # Qwen/Qwen2.5-7B-Instruct-AWQ on HF (2026-11-06): safetensors files total 5.19 GiB
    got = weight_bytes({"I32": 6_525_288_448, "F16": 1_090_328_064}) / 2**30
    assert abs(got - 5.19) / 5.19 < 0.05
