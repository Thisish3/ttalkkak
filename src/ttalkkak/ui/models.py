from collections.abc import Callable

from ttalkkak.plan.memory import usable_bytes
from ttalkkak.schemas import GpuInfo

_BYTES = {"BF16": 2, "F16": 2, "FP16": 2, "I8": 1, "U8": 1, "F8_E4M3": 1,
          "F8_E5M2": 1, "F32": 4,
          # HF counts AWQ/GPTQ int4 weights (packed into int32) as unpacked I32
          # parameters, so each is half a byte
          "I32": 0.5}  # fmt: skip


def weight_bytes(parameters: dict[str, int]) -> int:
    """Bytes of the safetensors weights; unknown dtypes count as 2 bytes."""
    return int(sum(n * _BYTES.get(dtype.upper(), 2) for dtype, n in parameters.items()))


def search_models(
    q: str, limit: int, gpu: GpuInfo | None, list_models: Callable
) -> list[dict]:
    rows = []
    found = list_models(
        search=q,
        pipeline_tag="text-generation",
        sort="downloads",
        limit=limit,
        expand=["safetensors", "downloads"],
    )
    for m in found:
        st = getattr(m, "safetensors", None)
        size = weight_bytes(dict(st.parameters)) if st and st.parameters else None
        fits = None if size is None or gpu is None else size < usable_bytes(gpu)
        rows.append(
            {
                "id": m.id,
                "downloads": getattr(m, "downloads", None),
                "weight_gib": None if size is None else size / 2**30,
                "fits": fits,
            }
        )
    return rows
