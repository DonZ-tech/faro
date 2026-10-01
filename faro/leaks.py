"""Detección de filtraciones: brechas conocidas (HIBP), código expuesto en GitHub y dorks.

Mismo contrato que la captación de influencia: cada respuesta se guarda como evidencia con
SHA-256, los emails se seudonimizan (el real solo en `identities`) y la salida va por el proxy
de la campaña. Lo que no tiene API pública fiable (buscadores web, pastes) no se automatiza:
los dorks se generan para abrirlos a mano.
"""
from __future__ import annotations
import re
import time
from urllib.parse import quote, urlencode

import httpx

from .campaign import Campaign
from .db import connect
from .evidence import store_raw, utcnow
from .paths import campaign_data
from .pseudo import pseudonym
from . import settings

UA = "faro-osint"
HIBP = "https://haveibeenpwned.com/api/v3"
GITHUB = "https://api.github.com"

# La búsqueda de código por API no admite OR ni paréntesis entre cualificadores: una consulta por
# indicio. Cada una cuesta una petición de las 10 por minuto.
GITHUB_HINTS = ("password", "smtp", "extension:env", "extension:sql")
# Una credencial asignada con valor, no la mera palabra: `password = "x8…"`, `SMTP_PASS: …`.
SECRET_ASSIGN = re.compile(
    r"(pass(word|wd)?|contraseña|secret|token|api[_-]?key|private[_-]?key|smtp[_-]?pass)\w*[\"']?\s*[:=]\s*[\"']?"
    r"(?!\s|\$\{|<|\*{3}|x{3}|changeme|example|your)[^\s\"',;]{6,}", re.I)


def looks_secret(text: str) -> bool:
    return bool(SECRET_ASSIGN.search(text or ""))


class LeakError(RuntimeError):
    """Fallo que el usuario puede arreglar (falta clave, límite de la API…)."""


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True, proxy=settings.proxy_url("http"),
                        headers={"User-Agent": UA})


def _upsert(con, leak_id: str, source: str, target: str, now: str, raw: tuple, **fields) -> bool:
    """Inserta o refresca un hallazgo. Devuelve True si es nuevo."""
    seen = con.execute("SELECT 1 FROM leaks WHERE id=?", (leak_id,)).fetchone()
    if seen:
        con.execute("UPDATE leaks SET last_seen=?, raw_path=?, raw_sha256=? WHERE id=?",
                    (now, str(raw[0]), raw[1], leak_id))
        return False
    con.execute("""INSERT INTO leaks(id, source, target, title, url, leak_date, data_classes, sensitive,
                   first_seen, last_seen, raw_path, raw_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (leak_id, source, target, fields.get("title"), fields.get("url"), fields.get("leak_date"),
                 fields.get("data_classes"), int(bool(fields.get("sensitive"))), now, now, str(raw[0]), raw[1]))
    return True


# ---------- HIBP: cuentas (requiere clave de pago) ----------
def hibp_accounts(camp: Campaign, emails: list[str], api_key: str | None = None, pause: float = 6.5) -> dict:
    """Brechas en las que aparece cada email. La API v3 exige la cabecera `hibp-api-key`;
    el plan básico permite 10 consultas por minuto, de ahí la pausa."""
    key = api_key or settings.load().get("FARO_HIBP_API_KEY")
    if not key:
        raise LeakError("Falta FARO_HIBP_API_KEY (https://haveibeenpwned.com/API/Key). "
                        "Sin clave, usa `faro leaks breaches` para las brechas de un dominio.")
    data, now = campaign_data(camp.name), utcnow()
    stats = {"emails": 0, "found": 0, "new": 0, "errors": 0}
    with _client() as http, connect(data) as con:
        for i, email in enumerate(e.strip().lower() for e in emails if e.strip()):
            if i:
                time.sleep(pause)
            ps = pseudonym("email", email)
            con.execute("INSERT OR IGNORE INTO identities(pseudo, platform, handle, first_seen) VALUES (?,?,?,?)",
                        (ps, "email", email, now))
            stats["emails"] += 1
            r = http.get(f"{HIBP}/breachedaccount/{quote(email)}", params={"truncateResponse": "false"},
                         headers={"hibp-api-key": key})
            if r.status_code == 404:
                continue
            if r.status_code == 401:
                raise LeakError("HIBP rechaza la clave (401).")
            if r.status_code != 200:
                stats["errors"] += 1
                continue
            breaches = r.json()
            raw = store_raw(data, "hibp", f"{ps}.breaches", {"target": ps, "breaches": breaches})
            stats["found"] += 1
            for b in breaches:
                stats["new"] += _upsert(con, f"hibp:{ps}:{b.get('Name')}", "hibp", ps, now, raw,
                                        title=b.get("Title") or b.get("Name"), url=b.get("Domain"),
                                        leak_date=b.get("BreachDate"),
                                        data_classes=", ".join(b.get("DataClasses", [])),
                                        sensitive="Passwords" in b.get("DataClasses", []))
    return stats


# ---------- HIBP: brechas de un dominio (gratis, sin clave) ----------
def hibp_domain_breaches(camp: Campaign, domains: list[str]) -> dict:
    """Brechas sufridas por el servicio que vive en cada dominio (p. ej. la web de un cliente)."""
    data, now = campaign_data(camp.name), utcnow()
    stats = {"domains": 0, "found": 0, "new": 0, "errors": 0}
    with _client() as http, connect(data) as con:
        for dom in (d.strip().lower() for d in domains if d.strip()):
            stats["domains"] += 1
            r = http.get(f"{HIBP}/breaches", params={"domain": dom})
            if r.status_code != 200:
                stats["errors"] += 1
                continue
            breaches = r.json()
            if not breaches:
                continue
            raw = store_raw(data, "hibp", f"{dom}.domain-breaches", {"domain": dom, "breaches": breaches})
            stats["found"] += 1
            for b in breaches:
                stats["new"] += _upsert(con, f"hibp-domain:{dom}:{b.get('Name')}", "hibp-domain", dom, now, raw,
                                        title=b.get("Title") or b.get("Name"), url=dom,
                                        leak_date=b.get("BreachDate"),
                                        data_classes=", ".join(b.get("DataClasses", [])),
                                        sensitive="Passwords" in b.get("DataClasses", []))
    return stats


# ---------- GitHub: código expuesto (requiere token) ----------
def github_code(camp: Campaign, queries: list[str], token: str | None = None, pause: float = 6.5) -> dict:
    """Ficheros de código público que mencionan cada consulta (dominio, organización…) y tienen
    pinta de datos o credenciales. La búsqueda de código de GitHub exige token desde 2023 y permite
    10 consultas por minuto."""
    tok = token or settings.load().get("FARO_GITHUB_TOKEN")
    if not tok:
        raise LeakError("Falta FARO_GITHUB_TOKEN: la búsqueda de código de GitHub no admite consultas anónimas.")
    data, now = campaign_data(camp.name), utcnow()
    stats = {"queries": 0, "files": 0, "new": 0, "sensitive": 0, "errors": 0}
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.text-match+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    searches = [(q, f'"{q}" {hint}') for q in (x.strip() for x in queries if x.strip()) for hint in GITHUB_HINTS]
    with _client() as http, connect(data) as con:
        for i, (q, full) in enumerate(searches):
            if i:
                time.sleep(pause)
            r = http.get(f"{GITHUB}/search/code", params={"q": full, "per_page": 100}, headers=headers)
            if r.status_code in (401, 403):
                raise LeakError(f"GitHub responde {r.status_code}: token inválido o límite alcanzado.")
            if r.status_code != 200:
                stats["errors"] += 1
                stats.setdefault("last_error", f"{r.status_code} {r.text[:120]}")
                continue
            body = r.json()
            raw = store_raw(data, "github", f"{q}.code", {"query": full, "result": body})
            stats["queries"] += 1
            for it in body.get("items", []):
                frags = " ".join(m.get("fragment", "") for m in it.get("text_matches", []))
                sens = looks_secret(frags)
                repo = it.get("repository", {}).get("full_name", "?")
                stats["files"] += 1
                stats["sensitive"] += sens
                stats["new"] += _upsert(con, f"github:{repo}:{it.get('path')}", "github", q, now, raw,
                                        title=f"{repo}/{it.get('path')}", url=it.get("html_url"),
                                        sensitive=sens)
    return stats


# ---------- Dorks: se generan, no se lanzan ----------
ENGINES = {"google": "https://www.google.com/search", "bing": "https://www.bing.com/search",
           "duckduckgo": "https://duckduckgo.com/"}


def dorks(domain: str) -> list[tuple[str, str]]:
    """Búsquedas avanzadas para revisar a mano. Los buscadores prohíben automatizarlas."""
    d = domain.strip().lower()
    files = "filetype:sql OR filetype:csv OR filetype:xlsx OR filetype:env OR filetype:log OR filetype:bak"
    return [
        ("ficheros expuestos", f"site:{d} ({files})"),
        ("listados de directorio", f'site:{d} intitle:"index of"'),
        ("paneles y logins", f"site:{d} (inurl:admin OR inurl:login OR inurl:wp-admin OR inurl:phpmyadmin)"),
        ("errores con rutas", f'site:{d} ("Warning:" OR "Fatal error" OR "stack trace")'),
        ("código en GitHub", f'site:github.com "{d}" (password OR secret OR token OR smtp)'),
        ("pastes", f'(site:pastebin.com OR site:ghostbin.site OR site:rentry.co OR site:telegra.ph) "{d}"'),
        ("documentos de terceros", f'"@{d}" (filetype:xlsx OR filetype:csv OR filetype:pdf) -site:{d}'),
    ]


def dork_url(query: str, engine: str = "google") -> str:
    return f"{ENGINES.get(engine, ENGINES['google'])}?{urlencode({'q': query})}"


def listing(camp: Campaign, source: str | None = None, since: str | None = None, reveal: bool = False) -> list[dict]:
    q = """SELECT l.*, i.handle FROM leaks l LEFT JOIN identities i ON i.pseudo = l.target WHERE 1=1"""
    args: list = []
    if source:
        q += " AND l.source=?"; args.append(source)
    if since:
        q += " AND l.first_seen>=?"; args.append(since)
    q += " ORDER BY l.sensitive DESC, l.first_seen DESC"
    with connect(campaign_data(camp.name)) as con:
        rows = [dict(r) for r in con.execute(q, args)]
    for r in rows:
        if reveal and r.get("handle"):
            r["target"] = r["handle"]
        r.pop("handle", None)
    return rows
