from ttalkkak.chips import ChipConfig, chip_by_name, resolve_chip_name
from ttalkkak.schemas import GpuInfo


class Platform:
    def __init__(self, chip: ChipConfig | None = None, *, vendor: str | None = None):
        if chip is None:
            chip = chip_by_name(resolve_chip_name(vendor or "nvidia"))
        self.chip = chip

    @property
    def vendor(self) -> str:
        return self.chip.vendor.lower()

    def image(self, engine: str) -> str | None:
        spec = self.chip.engines.get(engine)
        return spec.image if spec else None

    def gpu_flags(self) -> list[str]:
        d = self.chip.docker
        return [*d.flags, *(f"--device={dev}" for dev in d.devices)]

    @classmethod
    def from_gpu(cls, g: GpuInfo | None) -> "Platform":
        if g is None:
            return cls(chip_by_name("nvidia-gpu"))
        return cls(chip_by_name(resolve_chip_name(g.vendor, g.chip)))
