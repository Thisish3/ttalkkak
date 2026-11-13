from pathlib import Path

import pytest
from typer.testing import CliRunner

from ttalkkak import cli, ops_cli
from ttalkkak.errors import TtalkkakError
from ttalkkak.export import write_export

RUNS = Path(__file__).parent / "fixtures/runs"


def test_write_export_writes_files_and_readme(tmp_path):
    out = tmp_path / "deploy"
    written = write_export(RUNS / "vllm-nvidia", "k8s", out)
    names = sorted(p.name for p in written)
    assert names == ["README.md", "deployment.yaml", "service.yaml"]
    readme = (out / "README.md").read_text()
    assert "kubectl create secret generic hf-token" in readme
    assert "vllm-nvidia" in readme and "kubectl apply -f" in readme


def test_existing_files_need_force(tmp_path):
    out = tmp_path / "deploy"
    write_export(RUNS / "vllm-nvidia", "k8s", out)
    with pytest.raises(TtalkkakError, match="--force"):
        write_export(RUNS / "vllm-nvidia", "k8s", out)
    write_export(RUNS / "vllm-nvidia", "k8s", out, force=True)


def test_llmd_readme_uses_kustomize(tmp_path):
    write_export(RUNS / "vllm-nvidia", "llmd", tmp_path / "d")
    assert "kubectl apply -k" in (tmp_path / "d" / "README.md").read_text()


def test_unknown_target(tmp_path):
    with pytest.raises(TtalkkakError, match="target"):
        write_export(RUNS / "vllm-nvidia", "helm", tmp_path / "d")


def test_cli_export_exit_codes(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    import shutil

    shutil.copytree(RUNS / "vllm-nvidia", runs / "r1")
    monkeypatch.setattr(ops_cli, "ROOT", tmp_path)
    r = CliRunner().invoke(
        cli.app,
        [
            "ops",
            "export",
            "--run",
            "r1",
            "--target",
            "k8s",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert r.exit_code == 0 and (tmp_path / "out" / "deployment.yaml").exists()
    r = CliRunner().invoke(
        cli.app,
        [
            "ops",
            "export",
            "--run",
            "r1",
            "--target",
            "kserve",
            "--out",
            str(tmp_path / "o2"),
        ],
    )
    assert r.exit_code == 0
    r = CliRunner().invoke(
        cli.app,
        [
            "ops",
            "export",
            "--run",
            "r1",
            "--target",
            "dynamo",
            "--out",
            str(tmp_path / "o3"),
        ],
    )
    assert r.exit_code == 2


def test_write_export_chip_override_and_unknown(tmp_path):
    written = write_export(RUNS / "vllm-nvidia", "kserve", tmp_path / "a", chip="amd-gpu")
    text = (tmp_path / "a" / "llminferenceservice.yaml").read_text(encoding="utf-8")
    assert "amd.com/gpu" in text and written
    with pytest.raises(TtalkkakError, match="칩 설정을 찾을 수 없습니다: nope"):
        write_export(RUNS / "vllm-nvidia", "kserve", tmp_path / "b", chip="nope")


def test_ops_export_has_chip_option():
    from typer.testing import CliRunner

    from ttalkkak.cli import app

    r = CliRunner().invoke(app, ["ops", "export", "--help"])
    assert "--chip" in r.output
