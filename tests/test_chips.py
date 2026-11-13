from importlib.resources import files

import pytest

from ttalkkak import platform
from ttalkkak.chips import (
    builtin_chips,
    chip_by_name,
    json_path,
    load_chip,
)
from ttalkkak.errors import TtalkkakError
from ttalkkak.export import kserve, llmd


def test_builtin_values_match_old_constants():
    nv, amd = builtin_chips()
    assert (nv.name, amd.name) == ("nvidia-gpu", "amd-gpu")
    assert nv.engines["vllm"].image == "vllm/vllm-openai:v0.33.0"
    assert nv.engines["sglang"].image == "lmsysorg/sglang:v0.5.23"
    assert nv.docker.flags == ["--gpus", "all"]
    assert nv.kubernetes.resource == "nvidia.com/gpu"
    assert nv.export == {
        "kserve": "ghcr.io/llm-d/llm-d-cuda:v0.9.0",
        "llmd.vllm": "gpu-vllm/release",
        "llmd.sglang": "gpu-sglang/release",
    }
    assert nv.telemetry.exporter_image == "nvcr.io/nvidia/k8s/dcgm-exporter:4.9.0"
    assert nv.telemetry.metrics["power"] == "DCGM_FI_DEV_POWER_USAGE"
    for eng in ("vllm", "sglang"):
        assert amd.engines[eng].image == platform._IMAGES["amd"][eng]
        assert amd.export[f"llmd.{eng}"] == llmd._COMPONENT[("amd", eng)]
    assert amd.docker.flags == platform._FLAGS["amd"]
    assert amd.export["kserve"] == kserve._IMAGE["amd"]
    assert amd.kubernetes.resource == "amd.com/gpu"
    assert amd.telemetry.exporter_image is None
    assert amd.telemetry.metrics == {}


def test_example_npu_validates():
    p = files("ttalkkak.data").joinpath("chips/example-npu.yaml")
    chip = load_chip(p)
    assert chip.engines["vllm"].api == "openai"
    assert "example-npu" not in [c.name for c in builtin_chips()]


def test_missing_field_error_names_path(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text(
        files("ttalkkak.data").joinpath("chips/amd-gpu.yaml").read_text()
        .replace("engines:", "engines_x:")
    )
    with pytest.raises(TtalkkakError) as e:
        load_chip(f)
    assert "x.yaml" in str(e.value) and "engines" in str(e.value)


def test_bad_metric_key_rejected(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text(
        files("ttalkkak.data").joinpath("chips/amd-gpu.yaml").read_text()
        .replace("metrics: {}", "metrics: {fan: F}")
    )
    with pytest.raises(TtalkkakError) as e:
        load_chip(f)
    assert "telemetry.metrics.fan" in str(e.value)


def test_json_path():
    doc = {"devices": [{"name": "X"}]}
    assert json_path(doc, "$.devices[0].name") == "X"
    assert json_path(doc, "$.devices[1].name") is None
    assert json_path(doc, "$.nope") is None


def test_unknown_chip_name():
    with pytest.raises(TtalkkakError, match="칩 설정을 찾을 수 없습니다: nope"):
        chip_by_name("nope")
