from functools import cache
from importlib.resources import files

import yaml
from pydantic import BaseModel


class ParserEntry(BaseModel):
    tool: str | None
    reasoning: str | None


@cache
def _table() -> dict[str, ParserEntry]:
    raw = yaml.safe_load(files("ttalkkak.data").joinpath("parsers.yaml").read_text())
    return {k: ParserEntry.model_validate(v) for k, v in raw.items()}


def parsers_for(family: str) -> ParserEntry | None:
    return _table().get(family)
