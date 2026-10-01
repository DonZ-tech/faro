"""Captación vía yt-dlp: TikTok (hashtag/usuario/vídeo) y YouTube (búsqueda). Solo metadatos por
defecto; el vídeo se baja si --media. Cada JSON de metadatos se guarda como evidencia con hash."""
from __future__ import annotations
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..campaign import Campaign
from ..db import connect, upsert_account
from ..evidence import store_raw, utcnow, sha256_file
from ..pseudo import pseudonym
from ..paths import campaign_data
from .. import textnorm


def _proxy_args() -> list[str]:
    from ..settings import proxy_url
    p = proxy_url("ytdlp")
    return ["--proxy", p] if p else []


def _run_ytdlp(url: str, limit: int, media: bool, outdir: Path | None) -> list[dict]:
    cmd = ["yt-dlp", *_proxy_args(), "--ignore-errors", "--no-warnings", "--playlist-end", str(limit), "-J", "--flat-playlist"]
    if media:
        cmd = ["yt-dlp", *_proxy_args(), "--ignore-errors", "--no-warnings", "--playlist-end", str(limit), "--write-info-json",
               "-o", str(outdir / "%(extractor)s_%(id)s.%(ext)s"), url]
        subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=1800)
        out = []
        for j in outdir.glob("*.info.json"):
            try:
                out.append(json.loads(j.read_text()))
            except Exception:
                pass
        return out
    cmd.append(url)
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=900)
    if not r.stdout.strip():
        raise RuntimeError(r.stderr.strip()[-500:] or "yt-dlp sin salida")
    data = json.loads(r.stdout)
    if not data:
        raise RuntimeError(r.stderr.strip()[-500:] or "yt-dlp devolvió null")
    entries = (data.get("entries") or []) if data.get("_type") == "playlist" else [data]
    entries = [e for e in entries if e]
    # El listado plano no trae fecha: hidratar cada entrada en paralelo (sin descarga).
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_hydrate, entries))


def _hydrate(e: dict) -> dict:
    if e.get("timestamp") or e.get("upload_date"):
        return e
    url = e.get("webpage_url") or e.get("url")
    if not url:
        return e
    try:
        r = subprocess.run(["yt-dlp", *_proxy_args(), "--no-warnings", "--skip-download", "-J", url], capture_output=True, text=True, timeout=120)
        full = json.loads(r.stdout) if r.stdout.strip() else None
        if full:
            full.pop("formats", None); full.pop("requested_formats", None); full.pop("thumbnails", None)
            full.pop("automatic_captions", None); full.pop("subtitles", None); full.pop("heatmap", None)
            return full
    except Exception:
        pass
    return e


def _ingest(camp: Campaign, platform: str, entries: list[dict], target: str, media_dir: Path | None) -> int:
    data = campaign_data(camp.name)
    now = utcnow()
    n = 0
    with connect(data) as con:
        run_id = con.execute("INSERT INTO runs(started,platform,target,status) VALUES (?,?,?,?)",
                             (now, platform, target, "running")).lastrowid
        p, digest = store_raw(data, platform, f"{target.replace('/', '_')}.listing", {"target": target, "entries": entries, "captured_at": now})
        for e in entries:
            if not e or not e.get("id"):
                continue
            pid = f"{platform}:{e['id']}"
            if con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
                continue
            handle = e.get("uploader_id") or e.get("channel_id") or e.get("uploader") or e.get("channel") or "unknown"
            pseudo = pseudonym(platform, str(handle))
            upsert_account(con, pseudo, platform, str(handle), now, kind="user",
                           followers=e.get("channel_follower_count"),
                           title_hash=hashlib.sha256((e.get("uploader") or "").encode()).hexdigest())
            ts = e.get("timestamp")
            if ts:
                posted = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            elif e.get("upload_date"):
                posted = f"{e['upload_date'][:4]}-{e['upload_date'][4:6]}-{e['upload_date'][6:]}T00:00:00+00:00"
            else:
                # Sin fecha de publicación no se ingiere: una fecha inventada contamina la línea temporal.
                # La captura bruta queda guardada igual.
                continue
            if camp.since and posted[:10] < camp.since.isoformat():
                continue
            text = " ".join(filter(None, [e.get("title"), e.get("description")]))
            con.execute(
                """INSERT INTO posts(id,platform,account,posted_at,captured_at,text,text_norm,lang,url,views,forwards,replies,raw_path,raw_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, platform, pseudo, posted, now, text, textnorm.normalize(text), textnorm.guess_lang(text),
                 e.get("webpage_url") or e.get("url"), e.get("view_count"), e.get("repost_count"), e.get("comment_count"),
                 str(p), digest),
            )
            for u in textnorm.urls(text):
                dom = textnorm.domain_of(u)
                con.execute("INSERT INTO links(post_id,url,domain) VALUES (?,?,?)", (pid, u, dom))
                if dom:
                    con.execute("INSERT OR IGNORE INTO domains(domain, first_seen) VALUES (?,?)", (dom, now))
            mpath = None
            if media_dir:
                cands = list(media_dir.glob(f"*_{e['id']}.*"))
                cands = [c for c in cands if not c.name.endswith(".json")]
                if cands:
                    mpath = cands[0]
            con.execute("INSERT INTO media(post_id,kind,path,sha256,duration) VALUES (?,?,?,?,?)",
                        (pid, "video", str(mpath) if mpath else None, sha256_file(mpath) if mpath else None, e.get("duration")))
            n += 1
        con.execute("UPDATE runs SET finished=?, n_new=?, status='ok' WHERE id=?", (utcnow(), n, run_id))
    return n


def tiktok(camp: Campaign, targets: list[str] | None = None, limit: int = 100, media: bool = False) -> dict:
    """targets: hashtags (#x), usuarios (@x) o URLs de TikTok."""
    targets = targets or camp.seeds.tiktok_hashtags
    data = campaign_data(camp.name)
    stats = {"targets": 0, "new_posts": 0, "errors": []}
    for t in targets:
        if t.startswith("#"):
            url = f"https://www.tiktok.com/tag/{t[1:]}"
        elif t.startswith("@"):
            url = f"https://www.tiktok.com/{t}"
        else:
            url = t
        mdir = data / "raw" / "tiktok" / "media" if media else None
        if mdir:
            mdir.mkdir(parents=True, exist_ok=True)
        try:
            entries = _run_ytdlp(url, limit, media, mdir)
            if not entries and t.startswith("#"):
                stats["errors"].append((t, "TikTok no sirve listados por hashtag a yt-dlp ('No working app info'). "
                                           "Usa cuentas (@usuario) o URLs de vídeo como semilla; los hashtags sirven para el léxico."))
            stats["new_posts"] += _ingest(camp, "tiktok", entries, t, mdir)
            stats["targets"] += 1
        except Exception as e:
            stats["errors"].append((t, str(e)[-300:]))
    return stats


def youtube(camp: Campaign, queries: list[str] | None = None, limit: int = 50, media: bool = False) -> dict:
    queries = queries or camp.seeds.youtube_queries
    data = campaign_data(camp.name)
    stats = {"targets": 0, "new_posts": 0, "errors": []}
    for q in queries:
        url = q if q.startswith("http") else f"ytsearch{limit}:{q}"
        mdir = data / "raw" / "youtube" / "media" if media else None
        if mdir:
            mdir.mkdir(parents=True, exist_ok=True)
        try:
            entries = _run_ytdlp(url, limit, media, mdir)
            stats["new_posts"] += _ingest(camp, "youtube", entries, q, mdir)
            stats["targets"] += 1
        except Exception as e:
            stats["errors"].append((q, str(e)[-300:]))
    return stats
