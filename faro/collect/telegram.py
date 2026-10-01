"""Captación de canales públicos de Telegram con Telethon.

Solo canales/grupos públicos accesibles por username. La sesión vive en ~/.config/faro.
Snowball: cada forward desde otro canal público se anota como candidato en `discovered`.
"""
from __future__ import annotations
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..campaign import Campaign
from ..db import connect, upsert_account, add_edge
from ..evidence import store_raw, utcnow
from ..pseudo import pseudonym
from ..settings import load as load_creds, CRED_FILE
from ..paths import CONFIG_DIR, campaign_data
from .. import textnorm

SESSION = CONFIG_DIR / "telegram"


def _client():
    from telethon import TelegramClient
    creds = load_creds()
    api_id = creds.get("FARO_TG_API_ID")
    api_hash = creds.get("FARO_TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit(
            "Faltan FARO_TG_API_ID / FARO_TG_API_HASH. Créalos en https://my.telegram.org (API development tools)\n"
            f"y guárdalos con: faro auth telegram   (se escriben en {CRED_FILE})"
        )
    from ..settings import proxy_for_telethon
    return TelegramClient(str(SESSION), int(api_id), api_hash, proxy=proxy_for_telethon())


async def _login(phone: str) -> str:
    client = _client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        code = input("Código recibido en Telegram/SMS: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception as e:  # SessionPasswordNeededError
            if "password" in type(e).__name__.lower():
                pw = input("Contraseña 2FA de Telegram: ")
                await client.sign_in(password=pw)
            else:
                raise
    me = await client.get_me()
    await client.disconnect()
    return f"{me.first_name} (id {me.id})"


def login(phone: str) -> str:
    return asyncio.run(_login(phone))


def _msg_to_dict(m) -> dict:
    fwd = None
    if m.fwd_from is not None:
        fwd = {
            "from_id": getattr(getattr(m.fwd_from, "from_id", None), "channel_id", None),
            "from_name": getattr(m.fwd_from, "from_name", None),
            "channel_post": getattr(m.fwd_from, "channel_post", None),
            "date": m.fwd_from.date.isoformat() if m.fwd_from.date else None,
        }
    media_kind = None
    if m.video or (m.document and m.document.mime_type and m.document.mime_type.startswith("video")):
        media_kind = "video"
    elif m.photo:
        media_kind = "image"
    elif m.voice or m.audio:
        media_kind = "audio"
    elif m.document:
        media_kind = "doc"
    return {
        "id": m.id,
        "date": m.date.astimezone(timezone.utc).isoformat(),
        "message": m.message,
        "views": m.views,
        "forwards": m.forwards,
        "replies": m.replies.replies if m.replies else None,
        "fwd_from": fwd,
        "media": media_kind,
        "grouped_id": m.grouped_id,
        "post_author": m.post_author,
    }


async def _collect(camp: Campaign, targets: Iterable[str], limit: int, since: datetime | None, download_media: bool) -> dict:
    client = _client()
    await client.start()
    data = campaign_data(camp.name)
    now = utcnow()
    stats = {"channels": 0, "new_posts": 0, "discovered": set()}
    terms = camp.lexicon.all_terms()
    with connect(data) as con:
        for target in targets:
            t = target.strip().replace("https://t.me/", "").replace("t.me/", "").lstrip("@").strip("/")
            if not t:
                continue
            run_id = con.execute(
                "INSERT INTO runs(started,platform,target,status) VALUES (?,?,?,?)", (now, "telegram", t, "running")
            ).lastrowid
            try:
                entity = await client.get_entity(t)
                pseudo = pseudonym("telegram", t)
                full = None
                try:
                    from telethon.tl.functions.channels import GetFullChannelRequest
                    full = await client(GetFullChannelRequest(entity))
                except Exception:
                    pass
                followers = getattr(getattr(full, "full_chat", None), "participants_count", None)
                title = getattr(entity, "title", "") or ""
                upsert_account(
                    con, pseudo, "telegram", t, now,
                    kind="channel" if getattr(entity, "broadcast", False) else "group",
                    followers=followers,
                    title_hash=hashlib.sha256(title.encode()).hexdigest(),
                    seed=1 if t in [s.lstrip("@").replace("https://t.me/", "") for s in camp.seeds.telegram] else 0,
                )
                # Guardar metadatos del canal como evidencia
                store_raw(data, "telegram", f"{t}.channel", {
                    "username": t, "id": entity.id, "title": title, "participants": followers,
                    "date": getattr(entity, "date", None), "captured_at": now,
                })
                n = 0
                batch: list[dict] = []
                async for m in client.iter_messages(entity, limit=limit, offset_date=None, reverse=False):
                    if since and m.date.astimezone(timezone.utc) < since:
                        break
                    d = _msg_to_dict(m)
                    batch.append(d)
                    pid = f"telegram:{entity.id}:{m.id}"
                    exists = con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone()
                    if exists:
                        continue
                    fwd_pseudo = None
                    if d["fwd_from"] and d["fwd_from"]["from_id"]:
                        fwd_pseudo = pseudonym("telegram", f"id:{d['fwd_from']['from_id']}")
                        upsert_account(con, fwd_pseudo, "telegram", f"id:{d['fwd_from']['from_id']}", now, kind="channel")
                        add_edge(con, fwd_pseudo, pseudo, "forward", now)
                        stats["discovered"].add(f"id:{d['fwd_from']['from_id']}")
                    text = d["message"] or ""
                    con.execute(
                        """INSERT INTO posts(id,platform,account,posted_at,captured_at,text,text_norm,lang,url,views,forwards,replies,fwd_from)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (pid, "telegram", pseudo, d["date"], now, text, textnorm.normalize(text),
                         textnorm.guess_lang(text), f"https://t.me/{t}/{m.id}", d["views"], d["forwards"], d["replies"], fwd_pseudo),
                    )
                    for u in textnorm.urls(text):
                        dom = textnorm.domain_of(u)
                        con.execute("INSERT INTO links(post_id,url,domain) VALUES (?,?,?)", (pid, u, dom))
                        if dom == "t.me":
                            path = u.split("t.me/", 1)[1].split("/")[0].split("?")[0]
                            if path and not path.startswith("+") and path not in ("joinchat", "c", "s"):
                                stats["discovered"].add(path)
                        elif dom:
                            con.execute("INSERT OR IGNORE INTO domains(domain, first_seen) VALUES (?,?)", (dom, now))
                    for mention in textnorm.mentions(text):
                        mp = pseudonym("telegram", mention)
                        upsert_account(con, mp, "telegram", mention, now)
                        add_edge(con, pseudo, mp, "mention", now)
                        stats["discovered"].add(mention)
                    if d["media"] and download_media and (d["media"] in ("video", "image")) and textnorm.matches_lexicon(text, terms):
                        mdir = data / "raw" / "telegram" / "media"
                        mdir.mkdir(parents=True, exist_ok=True)
                        path = await client.download_media(m, file=str(mdir / f"{entity.id}_{m.id}"))
                        if path:
                            from ..evidence import sha256_file
                            con.execute("INSERT INTO media(post_id,kind,path,sha256) VALUES (?,?,?,?)",
                                        (pid, d["media"], path, sha256_file(Path(path))))
                    elif d["media"]:
                        con.execute("INSERT INTO media(post_id,kind) VALUES (?,?)", (pid, d["media"]))
                    n += 1
                if batch:
                    p, digest = store_raw(data, "telegram", f"{t}.messages", {"channel": t, "messages": batch, "captured_at": now})
                    con.execute("UPDATE posts SET raw_path=?, raw_sha256=? WHERE platform='telegram' AND account=? AND raw_path IS NULL",
                                (str(p), digest, pseudo))
                con.execute("UPDATE runs SET finished=?, n_new=?, status='ok' WHERE id=?", (utcnow(), n, run_id))
                stats["channels"] += 1
                stats["new_posts"] += n
            except Exception as e:
                con.execute("UPDATE runs SET finished=?, status='error', error=? WHERE id=?", (utcnow(), repr(e), run_id))
                stats.setdefault("errors", []).append((t, repr(e)))
            con.commit()
    await client.disconnect()
    stats["discovered"] = sorted(stats["discovered"])
    return stats


def collect(camp: Campaign, targets: list[str] | None = None, limit: int = 2000, download_media: bool = False) -> dict:
    targets = targets or camp.seeds.telegram
    since = datetime.combine(camp.since, datetime.min.time(), tzinfo=timezone.utc) if camp.since else None
    return asyncio.run(_collect(camp, targets, limit, since, download_media))


async def _search(camp: Campaign, terms: list[str], limit: int) -> list[dict]:
    """Búsqueda global de canales/mensajes públicos por término (SearchGlobal + contacts.Search)."""
    from telethon.tl.functions.contacts import SearchRequest
    client = _client()
    await client.start()
    found: dict[str, dict] = {}
    for term in terms:
        try:
            r = await client(SearchRequest(q=term, limit=limit))
            for ch in r.chats:
                if getattr(ch, "username", None):
                    found[ch.username] = {"username": ch.username, "title": ch.title, "term": term,
                                          "participants": getattr(ch, "participants_count", None)}
        except Exception as e:
            found[f"_err_{term}"] = {"error": repr(e), "term": term}
    await client.disconnect()
    return list(found.values())


def search(camp: Campaign, terms: list[str] | None = None, limit: int = 20) -> list[dict]:
    return asyncio.run(_search(camp, terms or camp.lexicon.all_terms(), limit))
