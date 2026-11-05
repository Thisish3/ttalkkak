from pathlib import Path

from ttalkkak.agent.runtime import mcp_config, system_prompt
from ttalkkak.skills.loader import load_skill

SK = Path(__file__).parent / "fixtures/skills"


def test_system_prompt_includes_each_skill_body():
    p = system_prompt([load_skill(SK / "tooly")])
    assert "## skill: tooly" in p and "Use tools." in p


def test_mcp_config_shape(tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: fx\ndescription: d\n---\nb\n")
    (d / "ttalkkak.yaml").write_text(
        "mcp_servers:\n  - name: fetch\n    command: [uvx, mcp-server-fetch]\n"
    )
    assert mcp_config([load_skill(d)]) == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }
