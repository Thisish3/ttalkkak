from importlib.resources import files

import pytest

from ttalkkak.chips import (
    builtin_chips,
    chip_by_name,
    json_path,
    load_chip,
)
from ttalkkak.errors import TtalkkakError


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
    assert amd.engines["vllm"].image == "vllm/vllm-openai-rocm:v0.33.0"
    assert amd.engines["sglang"].image == "lmsysorg/sglang:v0.5.23-rocm720-mi30x"
    assert amd.export["llmd.vllm"] == "amd-vllm/release"
    assert amd.export["llmd.sglang"] == "amd-sglang/rocm720-mi30x"
    assert amd.docker.flags[:2] == ["--device=/dev/kfd", "--device=/dev/dri"]
    assert amd.export["kserve"] == "ghcr.io/llm-d/llm-d-rocm:v0.9.0"
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


def _amd_text() -> str:
    return files("ttalkkak.data").joinpath("chips/amd-gpu.yaml").read_text(
        encoding="utf-8"
    )


def test_errors_are_korean_and_all_reported(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text(
        _amd_text()
        .replace("name: amd-gpu", "nme: amd-gpu")
        .replace("resource:", "resourc:")
        .replace("api: openai", "api: grpc", 1),
        encoding="utf-8",
    )
    with pytest.raises(TtalkkakError) as e:
        load_chip(f)
    msg = str(e.value)
    assert "name: 필수 칸이 없습니다" in msg
    assert "nme: 알 수 없는 칸입니다" in msg
    assert "engines.vllm.api: 허용되지 않는 값입니다" in msg and "openai" in msg
    assert "kubernetes.resource: 필수 칸이 없습니다" in msg
    assert len(msg.splitlines()) >= 4
    assert not any(w in msg for w in ("Field required", "Extra inputs"))


def test_type_error_is_korean(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text(_amd_text().replace("name: amd-gpu", "name: [1]"), encoding="utf-8")
    with pytest.raises(TtalkkakError, match="name: 형식이 맞지 않습니다"):
        load_chip(f)


def test_empty_engines_rejected(tmp_path):
    text = _amd_text()
    head, _, rest = text.partition("engines:")
    tail = rest[rest.index("docker:") :]
    f = tmp_path / "x.yaml"
    f.write_text(f"{head}engines: {{}}\n{tail}", encoding="utf-8")
    with pytest.raises(TtalkkakError, match="engines: 엔진이 하나 이상 필요합니다"):
        load_chip(f)


def test_chip_name_resolves_legacy_vendor():
    from ttalkkak.chips import resolve_chip_name

    assert resolve_chip_name("amd") == "amd-gpu"
    assert resolve_chip_name("nvidia") == "nvidia-gpu"
    assert resolve_chip_name("example-npu") == "example-npu"
    assert resolve_chip_name("amd", "nvidia-gpu") == "amd-gpu"
    assert resolve_chip_name("example", "example") == "example"
