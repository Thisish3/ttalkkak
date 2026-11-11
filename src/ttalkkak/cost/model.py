import math
from pathlib import Path

import yaml
from pydantic import BaseModel

J_PER_KWH = 3.6e6
MTOK = 1e6
SECONDS_PER_YEAR = 365 * 86400


class Slo(BaseModel):
    ttft_p99_ms: float = 1000
    itl_p99_ms: float = 100


class CostConfig(BaseModel):
    gpu_price_krw: float = 600000
    amortization_years: float = 3
    utilization: float = 0.6
    electricity_krw_per_kwh: float = 150
    pue: float = 1.4
    margin: float = 0.3
    slo: Slo = Slo()
    target_output_tok_s: float | None = None


class Point(BaseModel):
    streams: int
    output_tok_s: float
    ttft_p99_ms: float
    itl_p99_ms: float


class EnergyRun(BaseModel):
    energy_j: float
    input_tokens: int  # includes cached_tokens
    cached_tokens: int
    output_tokens: int


class Prices(BaseModel):
    input: float
    cached_input: float
    output: float


class CostResult(BaseModel):
    e_in_j: float
    e_cached_j: float
    e_out_j: float
    warnings: list[str]
    operating_point: Point | None
    gpus_needed: int | None
    cost_krw_per_mtok: Prices
    price_krw_per_mtok: Prices
    config: CostConfig


def solve_energy(
    a: EnergyRun, b: EnergyRun, c: EnergyRun | None
) -> tuple[float, float, float, list[str]]:
    warnings = []
    det = a.input_tokens * b.output_tokens - a.output_tokens * b.input_tokens
    scale = abs(a.input_tokens * b.output_tokens) or 1
    e_in = e_out = -1.0
    if abs(det) > 1e-9 * scale:
        e_in = (a.energy_j * b.output_tokens - a.output_tokens * b.energy_j) / det
        e_out = (a.input_tokens * b.energy_j - a.energy_j * b.input_tokens) / det
    if e_in < 0 or e_out < 0:
        warnings.append(
            "입력·출력 에너지 연립 해가 불안정해 B 측정으로 입력 에너지를 근사했습니다"
        )
        e_in = b.energy_j / b.input_tokens
        e_out = (a.energy_j - e_in * a.input_tokens) / a.output_tokens
        if e_out < 0:
            warnings.append("출력 토큰 에너지가 음수로 나와 0으로 두었습니다")
            e_out = 0.0
    e_cached = 0.0
    if c is not None and c.cached_tokens > 0:
        fresh = c.input_tokens - c.cached_tokens
        e_cached = (
            c.energy_j - e_in * fresh - e_out * c.output_tokens
        ) / c.cached_tokens
        if e_cached < 0:
            warnings.append("캐시 입력 토큰 에너지가 음수로 나와 0으로 두었습니다")
            e_cached = 0.0
    return e_in, e_cached, e_out, warnings


def operating_point(points: list[Point], slo: Slo) -> Point | None:
    ok = [
        p
        for p in points
        if p.ttft_p99_ms <= slo.ttft_p99_ms and p.itl_p99_ms <= slo.itl_p99_ms
    ]
    return max(ok, key=lambda p: p.streams, default=None)


def compute(
    cfg: CostConfig,
    a: EnergyRun,
    b: EnergyRun,
    c: EnergyRun | None,
    points: list[Point],
) -> CostResult:
    e_in, e_cached, e_out, warnings = solve_energy(a, b, c)
    op = operating_point(points, cfg.slo)
    basis = op
    if op is None and points:
        basis = min(points, key=lambda p: p.streams)
        warnings.append(
            "이 GPU로는 SLO를 지키는 운영점이 없어 "
            f"동시성 {basis.streams} 기준으로 상각비를 계산했습니다"
        )
    amort_out = 0.0
    if basis is not None and basis.output_tok_s > 0:
        per_s = cfg.gpu_price_krw / (
            cfg.amortization_years * SECONDS_PER_YEAR * cfg.utilization
        )
        amort_out = per_s / basis.output_tok_s
    else:
        warnings.append("처리량 측정이 없어 상각비를 빼고 계산했습니다")
    krw_per_j = cfg.electricity_krw_per_kwh * cfg.pue / J_PER_KWH

    def per_mtok(e: float) -> float:
        share = e / e_out if e_out > 0 else 0.0
        return (e * krw_per_j + amort_out * share) * MTOK

    cost = Prices(
        input=per_mtok(e_in), cached_input=per_mtok(e_cached), output=per_mtok(e_out)
    )
    price = Prices(**{k: v * (1 + cfg.margin) for k, v in cost.model_dump().items()})
    gpus = None
    if op is not None and cfg.target_output_tok_s and op.output_tok_s > 0:
        gpus = math.ceil(cfg.target_output_tok_s / op.output_tok_s)
    return CostResult(
        e_in_j=e_in,
        e_cached_j=e_cached,
        e_out_j=e_out,
        warnings=warnings,
        operating_point=op,
        gpus_needed=gpus,
        cost_krw_per_mtok=cost,
        price_krw_per_mtok=price,
        config=cfg,
    )


def load_or_create_config(path: Path) -> tuple[CostConfig, bool]:
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return CostConfig(**data), False
    cfg = CostConfig()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False), encoding="utf-8")
    return cfg, True
