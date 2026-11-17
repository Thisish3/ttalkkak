from ttalkkak.schemas import GpuInfo, ModelInfo

_BYTES = {"fp8": 1, "auto": 2}


def kv_bytes_per_token(m: ModelInfo, kv_dtype: str) -> int:
    return 2 * m.num_layers * m.num_kv_heads * m.head_dim * _BYTES[kv_dtype]


def usable_bytes(
    g: GpuInfo, utilization: float = 0.9, overhead_gib: float = 1.0
) -> int:
    return int(g.memory_mib * 2**20 * utilization) - int(overhead_gib * 2**30)


def max_context_fit(
    m: ModelInfo, g: GpuInfo, kv_dtype: str, utilization: float = 0.9
) -> int:
    free = usable_bytes(g, utilization) - m.weight_bytes
    return max(0, free // kv_bytes_per_token(m, kv_dtype))
