import os
import stat
import subprocess
import sys

from typer.testing import CliRunner

from ttalkkak.cli import app
from ttalkkak.client.config import ClientConfig, load_config, save_config

runner = CliRunner()


def test_base_import_does_not_pull_ops_dependencies():
    code = (
        "import sys, ttalkkak.cli;"
        "heavy=('langchain','starlette','guidellm','huggingface_hub','uvicorn');"
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_no_args_without_config_points_to_login(tmp_path, monkeypatch):
    monkeypatch.setattr("ttalkkak.client.config.HOME", tmp_path / ".ttalkkak")
    r = runner.invoke(app, [])
    assert r.exit_code == 2 and "ttalkkak login" in r.output


def test_ops_group_lists_operator_commands():
    r = runner.invoke(app, ["ops", "--help"])
    for name in ("up", "chat", "export", "ui"):
        assert name in r.output


def test_old_top_level_name_still_works_with_notice():
    r = runner.invoke(app, ["up", "--help"])
    assert r.exit_code == 0 and "ops up" in r.output


def test_config_file_is_private(tmp_path):
    p = save_config(ClientConfig(endpoint="https://gw"), tmp_path)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert load_config(tmp_path).endpoint == "https://gw"
