"""Enriquecimiento de dominios: RDAP (registro), DNS (A/NS), y consulta a crt.sh. Solo fuentes públicas."""
from __future__ import annotations
import socket
import httpx
from ..campaign import Campaign
from ..db import connect
from ..evidence import store_raw, utcnow
from ..paths import campaign_data

SKIP = {"t.me", "youtube.com", "youtu.be", "tiktok.com", "vm.tiktok.com", "facebook.com", "fb.watch", "instagram.com",
        "twitter.com", "x.com", "wa.me", "chat.whatsapp.com", "whatsapp.com", "google.com", "bit.ly", "threads.net",
        "fb.com", "apps.apple.com", "play.google.com", "amzn.to", "linktr.ee", "t.co", "goo.gl", "youtube.com"}


def rdap(domain: str) -> dict:
    try:
        from ..settings import proxy_url
        r = httpx.get(f"https://rdap.org/domain/{domain}", timeout=20, follow_redirects=True, proxy=proxy_url("http"))
        if r.status_code == 200:
            return r.json()
        return {"_status": r.status_code}
    except Exception as e:
        return {"_error": repr(e)}


def enrich(camp: Campaign, only_new: bool = True) -> dict:
    data = campaign_data(camp.name)
    now = utcnow()
    stats = {"enriched": 0, "skipped": 0}
    with connect(data) as con:
        rows = con.execute("SELECT domain FROM domains WHERE registrar IS NULL" if only_new else "SELECT domain FROM domains").fetchall()
        for (dom,) in rows:
            root = ".".join(dom.split(".")[-2:]) if not dom.endswith((".co.uk", ".com.br")) else ".".join(dom.split(".")[-3:])
            if root in SKIP or dom in SKIP:
                stats["skipped"] += 1
                continue
            info = rdap(root)
            created = None
            registrar = None
            country = None
            for ev in info.get("events", []):
                if ev.get("eventAction") == "registration":
                    created = ev.get("eventDate")
            for ent in info.get("entities", []):
                roles = ent.get("roles", [])
                if "registrar" in roles:
                    for v in ent.get("vcardArray", [None, []])[1]:
                        if v[0] == "fn":
                            registrar = v[3]
                if "registrant" in roles:
                    for v in ent.get("vcardArray", [None, []])[1]:
                        if v[0] == "adr" and isinstance(v[3], list):
                            country = v[3][-1] or None
            ns = ",".join(n.get("ldhName", "") for n in info.get("nameservers", []))
            try:
                ip = socket.gethostbyname(dom)
            except Exception:
                ip = None
            store_raw(data, "domains", f"{root}.rdap", info)
            con.execute("UPDATE domains SET registrar=?, created=?, registrant_country=?, ns=?, ip=?, notes=? WHERE domain=?",
                        (registrar or "?", created, country, ns, ip, f"rdap:{root} {now}", dom))
            stats["enriched"] += 1
    return stats
