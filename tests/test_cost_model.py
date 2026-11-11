import math

import pytest

from ttalkkak.cost.model import (
    CostConfig,
    EnergyRun,
    Point,
    Slo,
    compute,
    load_or_create_config,
    operating_point,
    solve_energy,
)

A = EnergyRun(
    energy_j=1 * 2560 + 5 * 1280, input_tokens=2560, cached_tokens=0, output_tokens=1280
)
B = EnergyRun(
    energy_j=1 * 20480 + 5 * 10, input_tokens=20480, cached_tokens=0, output_tokens=10
)


def test_solve_energy_recovers_known_values():
    e_in, _, e_out, w = solve_energy(A, B, None)
    assert e_in == pytest.approx(1) and e_out == pytest.approx(5) and w == []


def test_fallback_when_solution_negative():
    bad_b = EnergyRun(
        energy_j=10, input_tokens=20480, cached_tokens=0, output_tokens=10
    )
    e_in, _, e_out, w = solve_energy(A, bad_b, None)
    assert e_in >= 0 and e_out >= 0 and w
    assert e_in == pytest.approx(10 / 20480)


def test_cached_tokens_energy_from_third_run():
    c = EnergyRun(
        energy_j=1 * 2560 + 0.1 * 17920 + 5 * 1280,
        input_tokens=2560 + 17920,
        cached_tokens=17920,
        output_tokens=1280,
    )
    _, e_cached, _, w = solve_energy(A, B, c)
    assert e_cached == pytest.approx(0.1) and w == []


def test_negative_cached_energy_is_clamped_with_warning():
    c = EnergyRun(
        energy_j=1, input_tokens=20480, cached_tokens=17920, output_tokens=1280
    )
    _, e_cached, _, w = solve_energy(A, B, c)
    assert e_cached == 0 and w


def test_operating_point_is_largest_within_slo():
    pts = [
        Point(streams=s, output_tok_s=100 * s, ttft_p99_ms=100 * s, itl_p99_ms=20 * s)
        for s in (1, 2, 4, 8)
    ]
    assert operating_point(pts, Slo(ttft_p99_ms=500, itl_p99_ms=100)).streams == 4
    assert operating_point(pts, Slo(ttft_p99_ms=50, itl_p99_ms=100)) is None


def test_prices_and_gpus_needed():
    pts = [Point(streams=4, output_tok_s=400, ttft_p99_ms=100, itl_p99_ms=50)]
    r = compute(CostConfig(target_output_tok_s=1000), A, B, None, pts)
    elec_out = 5 / 3.6e6 * 150 * 1.4 * 1e6
    amort_out = 600000 / (3 * 365 * 86400 * 0.6) / 400 * 1e6
    assert r.cost_krw_per_mtok.output == pytest.approx(elec_out + amort_out)
    assert r.price_krw_per_mtok.output == pytest.approx(
        r.cost_krw_per_mtok.output * 1.3
    )
    assert r.cost_krw_per_mtok.input < r.cost_krw_per_mtok.output
    assert r.gpus_needed == math.ceil(1000 / 400)


def test_no_operating_point_means_no_gpu_count_and_a_warning():
    pts = [Point(streams=1, output_tok_s=50, ttft_p99_ms=5000, itl_p99_ms=500)]
    r = compute(CostConfig(target_output_tok_s=1000), A, B, None, pts)
    assert r.operating_point is None and r.gpus_needed is None
    assert any("SLO" in w for w in r.warnings)
    assert r.cost_krw_per_mtok.output > 0


def test_default_config_is_created_once(tmp_path):
    p = tmp_path / "cost.yaml"
    cfg, created = load_or_create_config(p)
    assert created and cfg.pue == 1.4 and p.exists()
    p.write_text(p.read_text().replace("pue: 1.4", "pue: 1.2"))
    cfg, created = load_or_create_config(p)
    assert not created and cfg.pue == 1.2


def test_zero_token_runs_raise_clean_error():
    from ttalkkak.errors import TtalkkakError

    zero = EnergyRun(energy_j=10, input_tokens=0, cached_tokens=0, output_tokens=0)
    with pytest.raises(TtalkkakError, match="토큰"):
        solve_energy(A, zero, None)
