"""Credenciales locales (~/.config/faro/credentials.env, 0600). Nunca en el repo ni en la campaña."""
from __future__ import annotations
import os
from pathlib import Path
from .paths import CONFIG_DIR

CRED_FILE = CONFIG_DIR / "credentials.env"


def load() -> dict[str, str]:
    out: dict[str, str] = {}
    if CRED_FILE.exists():
        for line in CRED_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items() if k.startswith("FARO_")})
    return out


def proxy_url(kind: str = "http") -> str | None:
    """Proxy de salida para la captación en clearnet. Por colector, con FARO_PROXY como comodín:
    FARO_PROXY_YTDLP (YouTube/TikTok: conviene IP residencial), FARO_PROXY_TELEGRAM (conviene IP fija
    de VPS), FARO_PROXY_HTTP (RDAP y web). No afecta a Tor (.onion), que usa FARO_TOR_PROXY.
    Si el proxy no acepta conexiones, se devuelve None y el colector sale directo (y lo avisa)."""
    c = load()
    u = c.get(f"FARO_PROXY_{kind.upper()}") or c.get("FARO_PROXY") or None
    if u and not proxy_reachable(u):
        import sys
        print(f"[faro] proxy {u} no responde; {kind} sale sin proxy", file=sys.stderr)
        return None
    return u


def proxy_reachable(u: str, timeout: float = 2.0) -> bool:
    import socket
    from urllib.parse import urlparse
    p = urlparse(u)
    try:
        with socket.create_connection((p.hostname, p.port or 1080), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_for_telethon():
    """Tupla (tipo, host, puerto[, rdns, user, pass]) para Telethon, o None."""
    u = proxy_url("telegram")
    if not u:
        return None
    from urllib.parse import urlparse
    import socks
    p = urlparse(u)
    kind = {"socks5": socks.SOCKS5, "socks5h": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}.get(p.scheme, socks.SOCKS5)
    return (kind, p.hostname, p.port or 1080, True, p.username, p.password)


def save(values: dict[str, str]) -> Path:
    cur = load()
    cur.update(values)
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text("".join(f"{k}={v}\n" for k, v in cur.items() if not k.startswith("_")))
    CRED_FILE.chmod(0o600)
    return CRED_FILE
