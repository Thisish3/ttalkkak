from ttalkkak.plan.memory import kv_bytes_per_token, max_context_fit, usable_bytes
from ttalkkak.schemas import GpuInfo, ModelInfo

QWEN7 = ModelInfo(
    model_id="q",
    family="qwen2",
    num_layers=28,
    num_kv_heads=4,
    head_dim=128,
    dtype="float16",
    quantization="awq",
    max_position=32768,
    weight_bytes=5_580_000_000,
)
GPU8 = GpuInfo(name="RTX 3060 Ti", memory_mib=8192, compute_capability="8.6")


def test_kv_bytes_formula():
    assert kv_bytes_per_token(QWEN7, "auto") == 57_344
    assert kv_bytes_per_token(QWEN7, "fp8") == 28_672


def test_usable_bytes():
    assert usable_bytes(GPU8) == int(8192 * 2**20 * 0.9) - 2**30


def test_fp8_roughly_doubles_context():
    a, b = max_context_fit(QWEN7, GPU8, "auto"), max_context_fit(QWEN7, GPU8, "fp8")
    assert a > 0 and abs(b - 2 * a) <= 1


def test_does_not_go_negative():
    big = QWEN7.model_copy(update={"weight_bytes": 20 * 2**30})
    assert max_context_fit(big, GPU8, "auto") == 0
