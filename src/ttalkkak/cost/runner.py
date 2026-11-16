import json
import subprocess
from pathlib import Path

from ttalkkak.chips import ChipConfig, chip_by_name
from ttalkkak.cost.model import (
    CostResult,
    EnergyRun,
    Point,
    compute,
    load_or_create_config,
)
from ttalkkak.cost.power import Sample, energy_j, parse_nvsmi_csv, power_cmd
from ttalkkak.errors import TtalkkakError
from ttalkkak.export.common import load_run
from ttalkkak.pipeline import resolve_endpoint

STREAMS = [1, 2, 4, 8, 16]
WORKLOADS = {
    "A": ("kind=synthetic_text,prompt_tokens=256,output_tokens=128", STREAMS),
    "B": ("kind=synthetic_text,prompt_tokens=2048,output_tokens=1", [8]),
    "C": (
        (
            "kind=synthetic_text,prompt_tokens=256,output_tokens=128,"
            "prefix_tokens=1792,prefix_count=1"
        ),
        [4],
    ),
}


def guidellm_cmd(
    base_url: str,
    model: str,
    data: str,
    out: Path,
    streams: list[int],
    max_seconds: int,
    api_key: str | None = None,
) -> list[str]:
    target = base_url.removesuffix("/v1")
    backend = f"kind=openai_http,target={target},model={model}"
    if api_key:
        backend += f",api_key={api_key}"
    return [
        "guidellm", "run",
        "--backend", backend,
        "--profile", "kind=concurrent",
        "--override", "profile.streams", ",".join(map(str, streams)),
        "--constraint", f"kind=max_duration,seconds={max_seconds}",
        "--data", data,
        "--tokenizer", f"kind=hf_auto,model={model}",
        "--output", f"kind=json,path={out}",
        "--disable-console-interactive", "--disable-progress",
    ]  # fmt: skip


def _successful(b: dict, key: str) -> dict:
    return b["metrics"][key]["successful"]


def parse_points(report: dict) -> list[Point]:
    pts = []
    for b in report["benchmarks"]:
        strategy = b["config"]["strategy"]
        pts.append(
            Point(
                streams=strategy.get("streams") or strategy.get("max_concurrency") or 1,
                output_tok_s=float(_successful(b, "output_tokens_per_second")["mean"]),
                ttft_p99_ms=float(
                    _successful(b, "time_to_first_token_ms")["percentiles"]["p99"]
                ),
                itl_p99_ms=float(
                    _successful(b, "inter_token_latency_ms")["percentiles"]["p99"]
                ),
            )
        )
    return sorted(pts, key=lambda p: p.streams)


def parse_stage_load(benchmark: dict) -> tuple[float, float]:
    """(tokens_per_min, error_rate) of one guidellm benchmark (stage)."""
    m = benchmark["metrics"]

    def total(key: str) -> float:
        return float(m[key]["successful"]["total_sum"])

    seconds = float(benchmark["duration"])
    tokens = total("prompt_token_count") + total("output_token_count")
    counts = m["request_totals"]
    requests = counts["total"]
    return (
        tokens / seconds * 60 if seconds > 0 else 0.0,
        counts["errored"] / requests if requests else 1.0,
    )


def parse_energy_run(report: dict, samples: list[Sample]) -> EnergyRun:
    def total(key: str) -> int:
        return int(sum(_successful(b, key)["total_sum"] for b in report["benchmarks"]))

    return EnergyRun(
        energy_j=sum(
            energy_j(samples, b["start_time"], b["end_time"])
            for b in report["benchmarks"]
        ),
        input_tokens=total("prompt_token_count"),
        cached_tokens=total("cached_token_count"),
        output_tokens=total("output_token_count"),
    )


def _markdown(r: CostResult, created: bool) -> str:
    c, p, cfg = r.cost_krw_per_mtok, r.price_krw_per_mtok, r.config
    op = r.operating_point
    lines = [
        "# 원가와 권장 가격 (추정)",
        "",
        "| 원/100만 토큰 | 원가 | 권장 가격 |",
        "|---|---:|---:|",
        f"| 입력 | {c.input:,.1f} | {p.input:,.1f} |",
        f"| 캐시 입력 | {c.cached_input:,.1f} | {p.cached_input:,.1f} |",
        f"| 출력 | {c.output:,.1f} | {p.output:,.1f} |",
        "",
        (
            f"토큰당 에너지: 입력 {r.e_in_j:.4f} J, 캐시 입력 {r.e_cached_j:.4f} J, "
            f"출력 {r.e_out_j:.4f} J"
        ),
        "",
        (
            f"운영점: 동시 요청 {op.streams}, 출력 {op.output_tok_s:,.0f} 토큰/초, "
            f"TTFT p99 {op.ttft_p99_ms:,.0f} ms, ITL p99 {op.itl_p99_ms:,.0f} ms"
            if op
            else "운영점: 이 GPU로는 SLO를 지킬 수 없음"
        ),
        f"필요 GPU 수: {r.gpus_needed}" if r.gpus_needed else "",
        "",
        "## 가정",
        "",
        (
            f"GPU {cfg.gpu_price_krw:,.0f}원, 상각 {cfg.amortization_years}년, "
            f"가동률 {cfg.utilization:.0%}, 전기 {cfg.electricity_krw_per_kwh}원/kWh, "
            f"PUE {cfg.pue}, 마진 {cfg.margin:.0%}, "
            f"SLO TTFT p99 {cfg.slo.ttft_p99_ms} ms · ITL p99 {cfg.slo.itl_p99_ms} ms"
        )
        + (" (cost.yaml 기본값)" if created else ""),
        "",
        "## 한계",
        "",
        "- GPU 보드 전력만 잽니다. 나머지 서버 전력은 PUE로 대신합니다.",
        "- 원가는 작업량(입력·출력 길이, 동시성)에 따라 달라집니다.",
        "- 상각비의 입력·출력 분배는 에너지 비율을 쓴 근사입니다.",
    ]
    if r.warnings:
        lines += ["", "## 경고", "", *[f"- {w}" for w in r.warnings]]
    return "\n".join(lines) + "\n"


def run_cost(
    run_dir: Path,
    cfg_path: Path,
    run=subprocess.run,
    popen=subprocess.Popen,
    max_seconds: int = 60,
    target_output_tok_s: float | None = None,
    chip: ChipConfig | None = None,
) -> tuple[CostResult, bool]:
    chip = chip or chip_by_name(load_run(run_dir).vendor)
    cmd = power_cmd(chip)
    if cmd is None:
        raise TtalkkakError("이 칩은 전력 측정 명령이 없습니다", 2)
    cfg, created = load_or_create_config(cfg_path)
    if target_output_tok_s is not None:
        cfg = cfg.model_copy(update={"target_output_tok_s": target_output_tok_s})
    base_url, model, _ = resolve_endpoint(run_dir)
    work = run_dir / "cost"
    work.mkdir(parents=True, exist_ok=True)
    power = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    reports, warnings = {}, []
    try:
        for name, (data, streams) in WORKLOADS.items():
            out = work / f"guidellm-{name}.json"
            try:
                run(
                    guidellm_cmd(base_url, model, data, out, streams, max_seconds),
                    check=True,
                    timeout=max_seconds * len(streams) * 3 + 600,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if name != "C":
                    raise
                warnings.append(
                    "캐시 입력 측정(C)이 실패해 캐시 입력 에너지를 0으로 두었습니다"
                )
                continue
            reports[name] = json.loads(out.read_text())
    finally:
        power.terminate()
        csv_text = power.communicate(timeout=10)[0] or ""
        (work / "power.csv").write_text(csv_text)
    samples = parse_nvsmi_csv(csv_text)
    a, b = (parse_energy_run(reports[k], samples) for k in "AB")
    c = None
    if "C" in reports:
        try:
            c = parse_energy_run(reports["C"], samples)
        except TtalkkakError as err:
            warnings.append(f"캐시 입력 측정(C)을 버렸습니다: {err}")
    result = compute(cfg, a, b, c, parse_points(reports["A"]))
    result.warnings[:0] = warnings
    (run_dir / "cost.json").write_text(result.model_dump_json(indent=1))
    (run_dir / "cost.md").write_text(_markdown(result, created), encoding="utf-8")
    return result, created
