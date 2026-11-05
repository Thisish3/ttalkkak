import json
import subprocess
from pathlib import Path


def perf_cmd(
    base_url: str, model: str, out_file: Path, requests: int = 20
) -> list[str]:
    target = base_url.removesuffix("/v1")
    return [
        "guidellm",
        "run",
        "--backend",
        f"kind=openai_http,target={target},model={model}",
        "--profile",
        "kind=synchronous",
        "--constraint",
        f"kind=max_requests,count={requests}",
        "--data",
        "kind=synthetic_text,prompt_tokens=256,output_tokens=128",
        "--tokenizer",
        f"kind=hf_auto,model={model}",
        "--output",
        f"kind=json,path={out_file}",
        "--disable-console-interactive",
        "--disable-progress",
    ]


def parse_perf(report: dict) -> dict[str, float]:
    m = report["benchmarks"][0]["metrics"]

    def mean(key):
        return float(m[key]["successful"]["mean"])

    return {
        "output_tok_s": mean("output_tokens_per_second"),
        "ttft_ms": mean("time_to_first_token_ms"),
        "itl_ms": mean("inter_token_latency_ms"),
        "req_s": mean("requests_per_second"),
    }


def run_perf(base_url, model, out_file: Path, run=subprocess.run) -> dict[str, float]:
    run(perf_cmd(base_url, model, out_file), check=True, timeout=1800)
    return parse_perf(json.loads(out_file.read_text()))
