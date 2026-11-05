import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ttalkkak.errors import TtalkkakError
from ttalkkak.schemas import Flag

_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    command: list[str]


class SkillExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requires: dict[Flag, bool] = Field(default_factory=dict)
    min_context: int = 0
    mcp_servers: list[McpServer] = Field(default_factory=list)


class Skill(BaseModel):
    name: str
    description: str
    body: str
    path: Path
    ext: SkillExt


def _split_front(text: str, path: Path) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise TtalkkakError(f"{path}: SKILL.md must start with YAML frontmatter")
    _, front, body = text.split("---\n", 2)
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise TtalkkakError(f"{path}: frontmatter must be a mapping")
    return data, body


def load_skill(path: Path) -> Skill:
    md = path / "SKILL.md"
    if not md.is_file():
        raise TtalkkakError(f"{path}: SKILL.md not found")
    front, body = _split_front(md.read_text(encoding="utf-8"), md)
    name, desc = str(front.get("name", "")), str(front.get("description", ""))
    if not (1 <= len(name) <= 64 and _NAME.match(name)):
        raise TtalkkakError(f"{md}: invalid skill name {name!r}")
    if name != path.name:
        raise TtalkkakError(f"{md}: name {name!r} must match directory {path.name!r}")
    if not 1 <= len(desc) <= 1024:
        raise TtalkkakError(f"{md}: description must be 1-1024 characters")
    ext_file = path / "ttalkkak.yaml"
    raw = (
        yaml.safe_load(ext_file.read_text(encoding="utf-8"))
        if ext_file.is_file()
        else {}
    )
    try:
        ext = SkillExt.model_validate(raw or {})
    except ValidationError as err:
        raise TtalkkakError(f"{ext_file}: {err}") from err
    return Skill(name=name, description=desc, body=body, path=path, ext=ext)


def load_skills(root: Path, names: list[str]) -> list[Skill]:
    return [load_skill(root / n) for n in names]
