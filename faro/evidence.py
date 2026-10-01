"""Cadena de custodia: cada captura bruta se guarda inmutable con SHA-256 y timestamp UTC."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_raw(base: Path, platform: str, name: str, payload: bytes | dict[str, Any]) -> tuple[Path, str]:
    """Guarda payload en data/raw/<platform>/<YYYY-MM-DD>/<name> y escribe <name>.sha256.

    Devuelve (ruta, hash). Nunca sobreescribe: si existe, añade sufijo.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = base / "raw" / platform / day
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        if not name.endswith(".json"):
            name += ".json"
    p = d / name
    i = 1
    while p.exists():
        p = d / f"{Path(name).stem}.{i}{Path(name).suffix}"
        i += 1
    p.write_bytes(payload)
    digest = sha256_bytes(payload)
    (p.with_suffix(p.suffix + ".sha256")).write_text(f"{digest}  {p.name}\n{utcnow()}\n")
    return p, digest


def verify_raw(base: Path) -> list[tuple[Path, bool]]:
    """Recorre data/raw y comprueba cada .sha256. Devuelve (ruta, ok)."""
    out = []
    for s in (base / "raw").rglob("*.sha256"):
        target = s.with_suffix("")
        if not target.exists():
            out.append((target, False))
            continue
        expected = s.read_text().split()[0]
        out.append((target, sha256_file(target) == expected))
    return out
