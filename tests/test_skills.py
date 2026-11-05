from pathlib import Path

import pytest

from ttalkkak.errors import TtalkkakError
from ttalkkak.skills.loader import load_skill
from ttalkkak.skills.requirements import merge_requirements


def make(tmp: Path, name: str, front: str, ext: str | None = None) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{front}\n---\n본문 지침\n")
    if ext is not None:
        (d / "ttalkkak.yaml").write_text(ext)
    return d


def test_loads_standard_skill_without_ext(tmp_path):
    s = load_skill(make(tmp_path, "a-b", "name: a-b\ndescription: does x"))
    assert s.name == "a-b" and s.body.strip() == "본문 지침"
    assert s.ext.requires == {} and s.ext.min_context == 0


@pytest.mark.parametrize("bad", ["A-b", "-ab", "ab-", "a--b", "x" * 65])
def test_rejects_bad_names(tmp_path, bad):
    d = make(tmp_path, "dir", f"name: {bad}\ndescription: d")
    with pytest.raises(TtalkkakError):
        load_skill(d)


def test_name_must_match_directory(tmp_path):
    with pytest.raises(TtalkkakError, match="directory"):
        load_skill(make(tmp_path, "other", "name: a-b\ndescription: d"))


def test_rejects_unknown_requirement_flag(tmp_path):
    d = make(tmp_path, "a", "name: a\ndescription: d", "requires:\n  telepathy: true\n")
    with pytest.raises(TtalkkakError):
        load_skill(d)


def test_merge_takes_union_and_max_with_sources(tmp_path):
    a = load_skill(
        make(
            tmp_path,
            "a",
            "name: a\ndescription: d",
            "requires:\n  tool_calling: true\nmin_context: 16384\n",
        )
    )
    b = load_skill(
        make(
            tmp_path,
            "b",
            "name: b\ndescription: d",
            "requires:\n  tool_calling: true\n  reasoning: true\nmin_context: 32768\n",
        )
    )
    r = merge_requirements([a, b])
    assert r.flags == {"tool_calling": True, "reasoning": True}
    assert r.min_context == 32768
    assert r.sources["tool_calling"] == ["a", "b"]
    assert r.sources["min_context"] == ["b"]
