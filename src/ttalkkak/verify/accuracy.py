import json
import subprocess
from pathlib import Path

from ttalkkak.errors import TtalkkakError

METRIC = "exact_match,flexible-extract"


def accuracy_cmd(
    base_url: str, model: str, out_dir: Path, limit: int = 100
) -> list[str]:
    model_args = (
        f"model={model},base_url={base_url}/chat/completions,"
        "num_concurrent=4,max_retries=3,tokenized_requests=False"
    )
    return [
        "lm_eval",
        "run",
        "--model",
        "local-chat-completions",
        "--model_args",
        model_args,
        "--tasks",
        "gsm8k",
        "--limit",
        str(limit),
        "--apply_chat_template",
        "--seed",
        "1234",
        "--output_path",
        str(out_dir),
    ]


def parse_accuracy(out_dir: Path) -> float:
    files = sorted(out_dir.rglob("results_*.json"))
    if not files:
        raise TtalkkakError(f"lm_eval 결과 파일이 없습니다: {out_dir}")
    return float(json.loads(files[-1].read_text())["results"]["gsm8k"][METRIC])


def run_accuracy(
    base_url, model, out_dir: Path, run=subprocess.run, limit=100
) -> float:
    run(accuracy_cmd(base_url, model, out_dir, limit), check=True, timeout=3600)
    return parse_accuracy(out_dir)
