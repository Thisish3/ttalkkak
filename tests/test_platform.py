from ttalkkak.platform import Platform
from ttalkkak.schemas import GpuInfo


def test_nvidia_images_and_flags():
    p = Platform(vendor="nvidia")
    assert p.image("vllm") == "vllm/vllm-openai:v0.33.0"
    assert p.image("sglang") == "lmsysorg/sglang:v0.5.23"
    assert p.gpu_flags() == ["--gpus", "all"]


def test_amd_images_and_flags_have_no_gpus_flag():
    p = Platform(vendor="amd")
    assert p.image("vllm") == "vllm/vllm-openai-rocm:v0.33.0"
    assert p.image("sglang") == "lmsysorg/sglang:v0.5.23-rocm720-mi30x"
    flags = p.gpu_flags()
    assert "--gpus" not in flags
    assert "--device=/dev/kfd" in flags and "--device=/dev/dri" in flags


def test_unknown_engine_has_no_image():
    assert Platform(vendor="nvidia").image("trtllm") is None


def test_from_gpu_defaults_to_nvidia():
    g = GpuInfo(name="MI300X", memory_mib=196608, compute_capability="", vendor="amd")
    assert Platform.from_gpu(g).vendor == "amd"
    assert Platform.from_gpu(None).vendor == "nvidia"
