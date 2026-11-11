from pathlib import Path

import pytest

from ttalkkak.cost.power import POWER_CMD, energy_j, parse_nvsmi_csv
from ttalkkak.errors import TtalkkakError

CSV = (Path(__file__).parent / "fixtures/power/nvsmi.csv").read_text()


def test_parse_and_trapezoid():
    s = parse_nvsmi_csv(CSV)
    assert [x.watts for x in s] == [100, 200, 200]
    assert s[0].sm_mhz == 1500 and s[2].temp_c == 52
    t0 = s[0].t
    assert energy_j(s, t0, t0 + 2) == pytest.approx(150 + 200)


def test_partial_interval_is_interpolated():
    s = parse_nvsmi_csv(CSV)
    t0 = s[0].t
    # 0.5..1.0: (150+200)/2*0.5 = 87.5, 1.0..1.5: 200*0.5 = 100
    assert energy_j(s, t0 + 0.5, t0 + 1.5) == pytest.approx(187.5)


def test_energy_requires_coverage():
    s = parse_nvsmi_csv(CSV)
    t0 = s[0].t
    with pytest.raises(TtalkkakError, match="전력 기록"):
        energy_j(s, t0 - 5, t0 + 2)
    with pytest.raises(TtalkkakError, match="전력 기록"):
        energy_j([], t0, t0 + 1)


def test_bad_rows_are_skipped():
    s = parse_nvsmi_csv(CSV + "2026/11/11 12:00:03.000, [N/A], 1500 MHz, 52\ngarbage\n")
    assert len(s) == 3


def test_power_cmd_samples_every_500ms_as_csv():
    assert POWER_CMD[0] == "nvidia-smi" and "-lms" in POWER_CMD and "500" in POWER_CMD
    assert any(a.startswith("--query-gpu=timestamp,power.draw") for a in POWER_CMD)


def test_large_gap_inside_window_is_rejected():
    rows = CSV.splitlines()[:2] + ["2026/11/11 12:00:09.500, 100.00 W, 1500 MHz, 50"]
    s = parse_nvsmi_csv("\n".join(rows))
    with pytest.raises(TtalkkakError, match="전력 기록"):
        energy_j(s, s[0].t, s[0].t + 9.5)
