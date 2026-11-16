"""Copy the shared console files from desktop/web into the package's static dir."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "desktop" / "web"
DST = ROOT / "src" / "ttalkkak" / "ui" / "static"
SYNCED = ("index.html", "app.css", "app.js", "connect.html", "connect.js")


def sync() -> None:
    for name in SYNCED:
        shutil.copyfile(SRC / name, DST / name)


if __name__ == "__main__":
    sync()
