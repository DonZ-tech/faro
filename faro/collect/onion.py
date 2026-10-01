"""Búsqueda y captura en servicios .onion vía Tor, adaptado de Robin (Apurv Singh Gautam, MIT).

Qué cambia respecto a Robin: sin Streamlit ni LangChain; httpx con proxy socks5h; cada página
capturada se guarda como evidencia (SHA-256 + timestamp) y entra en la BD como `post` de la
plataforma `onion`, con la cuenta = seudónimo del host. Solo lectura de páginas públicas: sin
login, sin formularios, sin descarga de binarios. Los motores se definen en la campaña
(`seeds.onion_engines`, plantillas con `{q}`); el paquete no trae ninguno cableado.
"""
from __future__ import annotations
import hashlib
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from ..campaign import Campaign
from ..db import connect, upsert_account
from ..evidence import store_raw, utcnow, sha256_bytes
from ..pseudo import pseudonym
from ..paths import campaign_data
from ..settings import load as load_creds
from .. import textnorm

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:137.0) Gecko/20100101 Firefox/137.0",
]
ONION_RE = re.compile(r"https?://[a-z2-7]{16,56}\.onion(?:[/?#][^\s\"'<>]*)?", re.I)
MAX_BYTES = 1_000_000
MAX_TEXT = 50_000
ALLOWED_CT = ("text/html", "application/xhtml+xml", "text/plain")


def tor_proxy() -> str:
    return load_creds().get("FARO_TOR_PROXY", "socks5h://127.0.0.1:9050")


def _client(timeout: float = 45.0) -> httpx.Client:
    return httpx.Client(proxy=tor_proxy(), timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=True,
                        headers={"User-Agent": random.choice(USER_AGENTS),
                                 "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"})


def tor_ok() -> tuple[bool, str]:
    """Comprueba que el proxy responde y que la salida es Tor."""
    try:
        with _client(30) as c:
            j = c.get("https://check.torproject.org/api/ip").json()
            return bool(j.get("IsTor")), j.get("IP", "?")
    except Exception as e:
        return False, repr(e)[:120]


def engines_for(camp: Campaign) -> dict[str, str]:
    out = {}
    for i, tpl in enumerate(camp.seeds.onion_engines):
        if "{q}" in tpl:
            out[urlparse(tpl).hostname[:12] if urlparse(tpl).hostname else f"e{i}"] = tpl
    return out


def parse_results(html: str, engine_url: str) -> list[dict]:
    """Parser genérico: cualquier <a> cuyo href sea .onion, que no sea el propio buscador ni otra búsqueda."""
    soup = BeautifulSoup(html, "html.parser")
    own = urlparse(engine_url).hostname or ""
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = ONION_RE.search(a["href"])
        if not m:
            continue
        link = m.group(0).rstrip("/")
        host = urlparse(link).hostname or ""
        title = a.get_text(" ", strip=True)
        if host == own or "search" in link.lower() or len(title) < 4 or link in seen:
            continue
        seen.add(link)
        out.append({"title": title[:200], "link": link, "host": host})
    return out


def search(camp: Campaign, queries: list[str], max_workers: int = 6) -> tuple[list[dict], dict]:
    """Cada consulta en cada motor de la campaña; la página de resultados se guarda como evidencia."""
    engines = engines_for(camp)
    data = campaign_data(camp.name)
    now = utcnow()
    hits: dict[str, dict] = {}
    stats = {"queries": len(queries), "engines": len(engines), "engines_ok": 0, "engines_fail": 0}
    if not engines:
        return [], stats

    def one(name: str, tpl: str, q: str):
        url = tpl.format(q=quote_plus(q))
        try:
            with _client() as c:
                r = c.get(url)
            if r.status_code != 200:
                return name, q, None, f"http {r.status_code}"
            store_raw(data, "onion", f"search_{name}_{hashlib.sha1(q.encode()).hexdigest()[:8]}.html", r.content)
            return name, q, parse_results(r.text, url), None
        except Exception as e:
            return name, q, None, repr(e)[:100]

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(one, n, t, q) for q in queries for n, t in engines.items()]
        for f in as_completed(futs):
            name, q, res, err = f.result()
            if err:
                stats["engines_fail"] += 1
                continue
            stats["engines_ok"] += 1
            for h in res:
                cur = hits.setdefault(h["link"], {**h, "queries": set(), "engines": set()})
                cur["queries"].add(q)
                cur["engines"].add(name)
    with connect(data) as con:
        con.execute("INSERT INTO runs(started,finished,platform,target,n_new,status,error) VALUES (?,?,?,?,?,?,?)",
                    (now, utcnow(), "onion", f"search:{len(queries)}q", len(hits), "ok" if stats["engines_ok"] else "error",
                     None if stats["engines_ok"] else "ningún motor respondió"))
    out = sorted(hits.values(), key=lambda h: (-len(h["engines"]), h["title"]))
    for h in out:
        h["queries"] = sorted(h["queries"])
        h["engines"] = sorted(h["engines"])
    return out, stats


def fetch_page(url: str) -> tuple[bytes | None, str | None, str | None]:
    """Descarga una página con límites de tamaño y tipo. Devuelve (bytes, texto, error)."""
    try:
        with _client() as c:
            with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return None, None, f"http {r.status_code}"
                ct = (r.headers.get("content-type") or "").lower()
                if ct and not any(t in ct for t in ALLOWED_CT):
                    return None, None, f"content-type {ct[:40]}"
                buf, n = [], 0
                for chunk in r.iter_bytes(8192):
                    n += len(chunk)
                    if n > MAX_BYTES:
                        break
                    buf.append(chunk)
        raw = b"".join(buf)
        soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.extract()
        text = " ".join(soup.get_text(" ").split())[:MAX_TEXT]
        return raw, text, None
    except Exception as e:
        return None, None, repr(e)[:100]


def scrape(camp: Campaign, hits: list[dict], limit: int = 30, max_workers: int = 5, only_relevant: bool = True) -> dict:
    """Captura las páginas de los hits, las guarda como evidencia y las ingiere como posts `onion`."""
    data = campaign_data(camp.name)
    terms = camp.lexicon.all_terms()
    now = utcnow()
    stats = {"fetched": 0, "ingested": 0, "errors": 0, "irrelevant": 0}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_page, h["link"]): h for h in hits[:limit]}
        with connect(data) as con:
            for f in as_completed(futs):
                h = futs[f]
                raw, text, err = f.result()
                if err or raw is None:
                    stats["errors"] += 1
                    continue
                stats["fetched"] += 1
                if only_relevant and not textnorm.matches_lexicon(text, terms) and not textnorm.matches_lexicon(h["title"], terms):
                    stats["irrelevant"] += 1
                    continue
                host = h["host"]
                p, digest = store_raw(data, "onion", f"page_{host[:16]}_{sha256_bytes(h['link'].encode())[:8]}.html", raw)
                pid = f"onion:{sha256_bytes(h['link'].encode())[:24]}"
                if con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
                    continue
                acc = pseudonym("onion", host)
                upsert_account(con, acc, "onion", host, now, kind="site",
                               title_hash=hashlib.sha256(h["title"].encode()).hexdigest())
                body = f"{h['title']}\n{text}"[:20000]
                con.execute("""INSERT INTO posts(id,platform,account,posted_at,captured_at,text,text_norm,lang,url,raw_path,raw_sha256)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (pid, "onion", acc, now, now, body, textnorm.normalize(body), textnorm.guess_lang(text[:2000]),
                             h["link"], str(p), digest))
                for u in textnorm.urls(text)[:50]:
                    dom = textnorm.domain_of(u)
                    con.execute("INSERT INTO links(post_id,url,domain) VALUES (?,?,?)", (pid, u, dom))
                    if dom and not dom.endswith(".onion"):
                        con.execute("INSERT OR IGNORE INTO domains(domain, first_seen) VALUES (?,?)", (dom, now))
                stats["ingested"] += 1
    return stats
