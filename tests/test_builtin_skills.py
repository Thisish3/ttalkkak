import subprocess
from pathlib import Path

import pytest

from ttalkkak.mcp_servers.files import list_dir, read_text
from ttalkkak.mcp_servers.pyexec import run_python
from ttalkkak.skills.loader import load_skill

ROOT = Path(__file__).parents[1] / "skills"


@pytest.mark.parametrize("name", ["web-fetch", "code-exec", "file-qa"])
def test_builtin_skills_load(name):
    s = load_skill(ROOT / name)
    assert s.ext.mcp_servers


def test_pyexec_runs_in_docker_without_network():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="2\n", stderr="")

    assert run_python("print(1+1)", run=run) == "2\n"
    assert seen["cmd"][:4] == ["docker", "run", "--rm", "--network=none"]


def test_files_refuse_escape(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    assert read_text("a.txt", tmp_path) == "hi"
    assert list_dir(".", tmp_path) == ["a.txt"]
    with pytest.raises(ValueError):
        read_text("../etc/passwd", tmp_path)
