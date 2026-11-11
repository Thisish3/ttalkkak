import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

HOME = Path.home() / ".ttalkkak"


class ClientConfig(BaseModel):
    endpoint: str
    issuer: str | None = None
    client_id: str = "ttalkkak-cli"
    model: str | None = None


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _home(home: Path | None) -> Path:
    return home if home is not None else HOME


def save_config(cfg: ClientConfig, home: Path | None = None) -> Path:
    lines = [f"{k} = {_toml_str(v)}" for k, v in cfg.model_dump().items() if v]
    path = _home(home) / "config.toml"
    write_private(path, "\n".join(lines) + "\n")
    return path


def load_config(home: Path | None = None) -> ClientConfig | None:
    path = _home(home) / "config.toml"
    if not path.is_file():
        return None
    return ClientConfig(**tomllib.loads(path.read_text(encoding="utf-8")))


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
