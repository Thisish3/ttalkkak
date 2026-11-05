import json
from pathlib import Path

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.probe import probe
from ttalkkak.probe.gpu import parse_nvidia_smi
from ttalkkak.probe.hf import fetch_model_info, model_info_from

FIX = Path(__file__).parent / "fixtures"


def test_model_info_from_qwen_awq():
    cfg = json.loads((FIX / "hf/qwen2.5-7b-instruct-awq.json").read_text())
    info = model_info_from(
        "Qwen/Qwen2.5-7B-Instruct-AWQ",
        cfg,
        {
            "model-00001.safetensors": 3_000,
            "model-00002.safetensors": 2_000,
            "tokenizer.json": 7,
        },
    )
    assert info.family == "qwen2" and info.num_layers == 28 and info.num_kv_heads == 4
    assert info.head_dim == cfg["hidden_size"] // cfg["num_attention_heads"]
    assert info.quantization == "awq" and info.weight_bytes == 5_000


def test_parse_nvidia_smi():
    g = parse_nvidia_smi("NVIDIA GeForce RTX 3060 Ti, 8192, 8.6\n")
    assert g.name == "NVIDIA GeForce RTX 3060 Ti" and g.memory_mib == 8192
    assert g.compute_capability == "8.6"


class _DeniedApi:
    def model_info(self, *a, **k):
        from huggingface_hub.errors import GatedRepoError

        raise GatedRepoError.__new__(GatedRepoError)


def test_gated_model_is_a_clear_error():
    with pytest.raises(TtalkkakError) as e:
        fetch_model_info("meta-llama/Llama-3.1-8B-Instruct", api=_DeniedApi())
    assert e.value.exit_code == 2 and "접근 불가" in str(e.value)


def test_nim_model_skips_hf_and_gpu():
    r = probe("nim:meta/llama-3.1-8b-instruct", api=object(), run=None)
    assert r.remote == "nim:meta/llama-3.1-8b-instruct" and r.model is None


def test_parse_amd_smi():
    from ttalkkak.probe.gpu import parse_amd_smi

    g = parse_amd_smi((FIX / "amd/amd_smi_static.json").read_text())
    assert g.vendor == "amd" and g.arch == "gfx942"
    assert g.name == "AMD Instinct MI300X" and g.memory_mib == 196592


def test_detect_falls_back_to_amd_smi():
    import subprocess

    from ttalkkak.probe.gpu import detect_gpu

    amd = (FIX / "amd/amd_smi_static.json").read_text()

    def run(cmd, **kw):
        if cmd[0] == "nvidia-smi":
            raise FileNotFoundError
        return subprocess.CompletedProcess(cmd, 0, stdout=amd, stderr="")

    g = detect_gpu(run)
    assert g is not None and g.vendor == "amd"


def test_no_gpu_tools_means_none():
    from ttalkkak.probe.gpu import detect_gpu

    def run(cmd, **kw):
        raise FileNotFoundError

    assert detect_gpu(run) is None
