"""Where MapMaker keeps its files: the tile cache, logs and self-test output.

Running from the source folder: <folder>/data. Installed (frozen) program:
Documents/MapMaker/data, so nothing is written to the program folder."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    env = os.environ.get("MAPMAKER_DATA")
    if env:
        d = Path(env)
    elif getattr(sys, "frozen", False):
        d = Path.home() / "Documents" / "MapMaker" / "data"
    else:
        d = ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def maps_dir() -> Path:
    """The default folder for saving maps."""
    d = Path.home() / "Documents" / "MapMaker" / "Maps"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = data_dir()
    return d


def setup_logging() -> Path:
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "mapmaker.log"
    root = logging.getLogger("mapmaker")
    if not root.handlers:
        h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    return path
