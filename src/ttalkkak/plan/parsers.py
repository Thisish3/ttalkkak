from functools import cache
from importlib.resources import files

import yaml
from pydantic import BaseModel


class ParserEntry(BaseModel):
    tool: str | None
    reasoning: str | None


@cache
def _table() -> dict[str, dict[str, ParserEntry]]:
    raw = yaml.safe_load(files("ttalkkak.data").joinpath("parsers.yaml").read_text())
    return {
        fam: {eng: ParserEntry.model_validate(e) for eng, e in engines.items()}
        for fam, engines in raw.items()
    }


def parsers_for(family: str, engine: str) -> ParserEntry | None:
    return _table().get(family, {}).get(engine)
