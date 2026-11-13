import shutil
from pathlib import Path

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.export.common import engine_args, gpu_key, load_run

RUNS = Path(__file__).parent / "fixtures/runs"


def test_load_run_returns_chosen_candidate_and_vendor():
    b = load_run(RUNS / "vllm-amd")
    assert b.candidate.id == "c1" and b.vendor == "amd-gpu" and b.run_id == "vllm-amd"


def test_unfinished_run_is_an_error(tmp_path):
    shutil.copytree(RUNS / "vllm-nvidia", tmp_path / "r")
    (tmp_path / "r" / "pick.json").unlink()
    with pytest.raises(TtalkkakError) as e:
        load_run(tmp_path / "r")
    assert e.value.exit_code == 2


def test_run_without_choice_is_an_error(tmp_path):
    shutil.copytree(RUNS / "vllm-nvidia", tmp_path / "r")
    (tmp_path / "r" / "pick.json").write_text('{"chosen": null}')
    with pytest.raises(TtalkkakError, match="선택"):
        load_run(tmp_path / "r")


@pytest.mark.parametrize("run", ["vllm-nvidia", "sglang-nvidia"])
def test_engine_args_drop_model_host_port(run):
    args = engine_args(load_run(RUNS / run).candidate)
    for flag in ["--model", "--model-path", "--host", "--port", "python3"]:
        assert flag not in args
    assert "--kv-cache-dtype" in args


def test_gpu_key():
    assert gpu_key("amd") == "amd.com/gpu" and gpu_key("nvidia") == "nvidia.com/gpu"
