"""Rutas. Todo lo sensible vive fuera del repo (~/.config/faro, ~/.local/share/faro)."""
from __future__ import annotations
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("FARO_CONFIG_DIR", Path.home() / ".config" / "faro"))
DATA_DIR = Path(os.environ.get("FARO_DATA_DIR", Path.home() / ".local" / "share" / "faro"))
CAMPAIGNS_DIR = Path(os.environ.get("FARO_CAMPAIGNS_DIR", Path.cwd() / "campaigns"))


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, CAMPAIGNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    os.chmod(DATA_DIR, 0o700)


def campaign_dir(name: str) -> Path:
    return CAMPAIGNS_DIR / name


def campaign_data(name: str) -> Path:
    """Datos brutos y DB de una campaña: fuera del repo."""
    d = DATA_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d
