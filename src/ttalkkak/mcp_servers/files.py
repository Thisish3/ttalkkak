import os
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("files")
ROOT = Path(os.environ.get("TTALKKAK_FILES_ROOT", ".")).resolve()


def _inside(path: str, root: Path) -> Path:
    p = (root / path).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"path outside root: {path}")
    return p


def read_text(path: str, root: Path = ROOT) -> str:
    return _inside(path, root).read_text(encoding="utf-8", errors="replace")[:200_000]


def list_dir(path: str, root: Path = ROOT) -> list[str]:
    return sorted(p.name for p in _inside(path, root).iterdir())


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file under the configured root."""
    return read_text(path)


@mcp.tool()
def list_files(path: str = ".") -> list[str]:
    """List entries of a directory under the configured root."""
    return list_dir(path)


if __name__ == "__main__":
    mcp.run()
