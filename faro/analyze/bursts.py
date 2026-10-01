"""Ráfagas: mismo contenido (texto normalizado, URL o media hash) publicado por >=N cuentas distintas
en una ventana de W minutos. Señal 1 de coordinación (necesita otra independiente para etiquetar)."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
import pandas as pd
from ..campaign import Campaign
from ..db import connect
from ..paths import campaign_data


def _windows(rows: list[tuple[str, str, str]], window_min: int, min_accounts: int) -> list[dict]:
    """rows: (key, account, posted_at). Devuelve ráfagas."""
    by_key: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for key, acc, ts in rows:
        try:
            by_key[key].append((datetime.fromisoformat(ts.replace("Z", "+00:00")), acc))
        except Exception:
            continue
    bursts = []
    for key, items in by_key.items():
        items.sort()
        i = 0
        while i < len(items):
            j = i
            accs = {items[i][1]}
            while j + 1 < len(items) and (items[j + 1][0] - items[i][0]).total_seconds() <= window_min * 60:
                j += 1
                accs.add(items[j][1])
            if len(accs) >= min_accounts:
                bursts.append({"key": key[:120], "start": items[i][0].isoformat(), "end": items[j][0].isoformat(),
                               "n_posts": j - i + 1, "n_accounts": len(accs), "accounts": sorted(accs)})
                i = j + 1
            else:
                i += 1
    return sorted(bursts, key=lambda b: (-b["n_accounts"], b["start"]))


def text_bursts(camp: Campaign, window_min: int = 10, min_accounts: int = 3, min_len: int = 25) -> list[dict]:
    with connect(campaign_data(camp.name)) as con:
        rows = con.execute("SELECT text_norm, account, posted_at FROM posts WHERE length(text_norm) >= ?", (min_len,)).fetchall()
    return _windows([tuple(r) for r in rows], window_min, min_accounts)


def url_bursts(camp: Campaign, window_min: int = 30, min_accounts: int = 3) -> list[dict]:
    with connect(campaign_data(camp.name)) as con:
        rows = con.execute("""SELECT l.url, p.account, p.posted_at FROM links l JOIN posts p ON p.id=l.post_id""").fetchall()
    return _windows([tuple(r) for r in rows], window_min, min_accounts)


def media_bursts(camp: Campaign, window_min: int = 120, min_accounts: int = 2) -> list[dict]:
    with connect(campaign_data(camp.name)) as con:
        rows = con.execute("""SELECT m.phash, p.account, p.posted_at FROM media m JOIN posts p ON p.id=m.post_id WHERE m.phash IS NOT NULL""").fetchall()
    return _windows([tuple(r) for r in rows], window_min, min_accounts)


def account_creation_clusters(camp: Campaign, window_days: int = 3, min_accounts: int = 3) -> pd.DataFrame:
    """Cuentas creadas en lote: >=N cuentas con created_at en una ventana de D días."""
    with connect(campaign_data(camp.name)) as con:
        df = pd.read_sql_query("SELECT pseudo, platform, created_at FROM accounts WHERE created_at IS NOT NULL", con)
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df = df.dropna().sort_values("created_at")
    df["bucket"] = df["created_at"].dt.floor(f"{window_days}D")
    g = df.groupby(["platform", "bucket"]).agg(n=("pseudo", "size"), accounts=("pseudo", lambda s: ",".join(s))).reset_index()
    return g[g["n"] >= min_accounts].sort_values("n", ascending=False)
