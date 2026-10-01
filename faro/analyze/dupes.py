"""Near-duplicates de texto (MinHash/LSH sobre shingles de caracteres, robusto a ar/es) y de media (pHash)."""
from __future__ import annotations
from pathlib import Path
import subprocess
from datasketch import MinHash, MinHashLSH
from ..campaign import Campaign
from ..db import connect, add_edge
from ..evidence import utcnow
from ..paths import campaign_data


def _shingles(t: str, k: int = 5) -> set[str]:
    t = t.replace(" ", "_")
    return {t[i:i + k] for i in range(max(1, len(t) - k + 1))}


def text_near_dupes(camp: Campaign, threshold: float = 0.7, min_len: int = 40, write_edges: bool = True) -> list[dict]:
    with connect(campaign_data(camp.name)) as con:
        rows = con.execute("SELECT id, account, text_norm FROM posts WHERE length(text_norm) >= ?", (min_len,)).fetchall()
        lsh = MinHashLSH(threshold=threshold, num_perm=128)
        mh: dict[str, MinHash] = {}
        acc: dict[str, str] = {}
        for pid, account, t in rows:
            m = MinHash(num_perm=128)
            for s in _shingles(t):
                m.update(s.encode("utf-8"))
            mh[pid] = m
            acc[pid] = account
            lsh.insert(pid, m)
        seen = set()
        pairs = []
        now = utcnow()
        for pid, m in mh.items():
            for other in lsh.query(m):
                if other == pid or acc[other] == acc[pid]:
                    continue
                key = tuple(sorted((pid, other)))
                if key in seen:
                    continue
                seen.add(key)
                sim = m.jaccard(mh[other])
                pairs.append({"a": pid, "b": other, "acc_a": acc[pid], "acc_b": acc[other], "sim": round(sim, 3)})
                if write_edges:
                    add_edge(con, acc[pid], acc[other], "dup_text", now)
    return sorted(pairs, key=lambda p: -p["sim"])


def _keyframe(video: Path, out: Path, at: float = 1.0) -> Path | None:
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", "-vf", "scale=320:-1", str(out)],
                       check=True, timeout=120, capture_output=True)
        return out if out.exists() else None
    except Exception:
        return None


def hash_media(camp: Campaign) -> int:
    """Calcula pHash para media descargada sin hash. Vídeo: frames en 1s, 25%, 50%, 75% (se guarda el del 50%,
    los demás alimentan la comparación por prefijo). Imagen: directo."""
    import imagehash
    from PIL import Image
    data = campaign_data(camp.name)
    frames = data / "frames"
    frames.mkdir(exist_ok=True)
    n = 0
    with connect(data) as con:
        rows = con.execute("SELECT id, kind, path, duration FROM media WHERE path IS NOT NULL AND phash IS NULL").fetchall()
        for mid, kind, path, dur in rows:
            p = Path(path)
            if not p.exists():
                continue
            try:
                if kind == "image":
                    h = imagehash.phash(Image.open(p))
                else:
                    at = (dur or 4) * 0.5
                    f = _keyframe(p, frames / f"{mid}.jpg", at)
                    if not f:
                        continue
                    h = imagehash.phash(Image.open(f))
                con.execute("UPDATE media SET phash=? WHERE id=?", (str(h), mid))
                n += 1
            except Exception:
                continue
    return n


def media_near_dupes(camp: Campaign, max_distance: int = 8, write_edges: bool = True) -> list[dict]:
    import imagehash
    with connect(campaign_data(camp.name)) as con:
        rows = con.execute("SELECT m.id, m.phash, p.account, p.id FROM media m JOIN posts p ON p.id=m.post_id WHERE m.phash IS NOT NULL").fetchall()
        items = [(mid, imagehash.hex_to_hash(ph), acc, pid) for mid, ph, acc, pid in rows]
        pairs = []
        now = utcnow()
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][2] == items[j][2]:
                    continue
                d = items[i][1] - items[j][1]
                if d <= max_distance:
                    pairs.append({"a": items[i][3], "b": items[j][3], "acc_a": items[i][2], "acc_b": items[j][2], "distance": int(d)})
                    if write_edges:
                        add_edge(con, items[i][2], items[j][2], "dup_media", now)
    return sorted(pairs, key=lambda p: p["distance"])
