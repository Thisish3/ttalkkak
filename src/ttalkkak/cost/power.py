from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from ttalkkak.chips import ChipConfig
from ttalkkak.errors import TtalkkakError

# Timestamps are local wall-clock time, so samples and guidellm's epoch
# start/end times must come from the same machine.


def power_cmd(chip: ChipConfig) -> list[str] | None:
    return list(chip.telemetry.power_watts) if chip.telemetry.power_watts else None


TS_FORMAT = "%Y/%m/%d %H:%M:%S.%f"


@dataclass
class Sample:
    t: float
    watts: float
    sm_mhz: float | None
    temp_c: float | None


def _num(text: str) -> float | None:
    try:
        return float(text.strip().split()[0])
    except (ValueError, IndexError):
        return None


def parse_nvsmi_csv(text: str) -> list[Sample]:
    out = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 2:
            continue
        try:
            t = datetime.strptime(cols[0], TS_FORMAT).timestamp()
        except ValueError:
            continue
        watts = _num(cols[1])
        if watts is None:
            continue
        sm = _num(cols[2]) if len(cols) > 2 else None
        temp = _num(cols[3]) if len(cols) > 3 else None
        out.append(Sample(t, watts, sm, temp))
    return sorted(out, key=lambda s: s.t)


def _watts_at(samples: list[Sample], t: float) -> float:
    for a, b in pairwise(samples):
        if a.t <= t <= b.t:
            if b.t == a.t:
                return a.watts
            return a.watts + (b.watts - a.watts) * (t - a.t) / (b.t - a.t)
    raise ValueError(t)


def energy_j(
    samples: list[Sample],
    t0: float,
    t1: float,
    min_coverage: float = 0.9,
    max_gap_s: float = 2.0,
) -> float:
    lo = max(t0, samples[0].t) if samples else t1
    hi = min(t1, samples[-1].t) if samples else t0
    if len(samples) < 2 or hi - lo < (t1 - t0) * min_coverage:
        raise TtalkkakError(
            f"전력 기록이 측정 구간의 {min_coverage:.0%}를 덮지 못했습니다"
        )
    inside = [s.t for s in samples if lo <= s.t <= hi]
    edges = [lo, *inside, hi]
    if max(b - a for a, b in pairwise(edges)) > max_gap_s:
        raise TtalkkakError(f"전력 기록에 {max_gap_s:g}초보다 긴 빈 구간이 있습니다")
    points = [(lo, _watts_at(samples, lo))]
    points += [(s.t, s.watts) for s in samples if lo < s.t < hi]
    points.append((hi, _watts_at(samples, hi)))
    return sum((w0 + w1) / 2 * (b - a) for (a, w0), (b, w1) in pairwise(points))
