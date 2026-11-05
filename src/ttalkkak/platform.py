from typing import Literal

from pydantic import BaseModel

from ttalkkak.schemas import GpuInfo

_IMAGES = {
    "nvidia": {
        "vllm": "vllm/vllm-openai:v0.33.0",
        "sglang": "lmsysorg/sglang:v0.5.23",
    },
    "amd": {
        "vllm": "vllm/vllm-openai-rocm:v0.33.0",
        "sglang": "lmsysorg/sglang:v0.5.23-rocm720-mi30x",
    },
}
_FLAGS = {
    "nvidia": ["--gpus", "all"],
    "amd": [
        "--device=/dev/kfd",
        "--device=/dev/dri",
        "--group-add",
        "video",
        "--group-add",
        "render",
        "--security-opt",
        "seccomp=unconfined",
    ],
}


class Platform(BaseModel):
    vendor: Literal["nvidia", "amd"]

    def image(self, engine: str) -> str | None:
        return _IMAGES[self.vendor].get(engine)

    def gpu_flags(self) -> list[str]:
        return list(_FLAGS[self.vendor])

    @classmethod
    def from_gpu(cls, g: GpuInfo | None) -> "Platform":
        return cls(vendor=g.vendor if g else "nvidia")
