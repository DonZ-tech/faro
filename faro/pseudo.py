"""Seudonimización: HMAC-SHA256 con clave local. El mapa real nunca sale del disco.

La clave vive en ~/.config/faro/pseudo.key (0600). Sin clave, no hay análisis:
así es imposible exportar handles reales por descuido.
"""
from __future__ import annotations
import hmac
import hashlib
import secrets
from .paths import CONFIG_DIR

KEY_FILE = CONFIG_DIR / "pseudo.key"


def ensure_key() -> bytes:
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    return key


def pseudonym(platform: str, handle: str, length: int = 12) -> str:
    """Seudónimo estable por (plataforma, handle normalizado)."""
    key = ensure_key()
    norm = f"{platform.lower()}:{handle.strip().lstrip('@').lower()}"
    digest = hmac.new(key, norm.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{platform[:2].lower()}_{digest[:length]}"
