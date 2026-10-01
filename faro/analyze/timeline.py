"""Volumen por día/plataforma y por término del léxico; figura con fechas clave."""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from ..campaign import Campaign
from ..db import connect
from ..paths import campaign_data
from ..textnorm import normalize


def daily(camp: Campaign) -> pd.DataFrame:
    with connect(campaign_data(camp.name)) as con:
        df = pd.read_sql_query("SELECT platform, substr(posted_at,1,10) AS day, text_norm FROM posts", con)
    if camp.since is not None and not df.empty:
        df = df[df["day"] >= camp.since.isoformat()]
    if df.empty:
        return df
    terms = [normalize(t) for t in camp.lexicon.all_terms()]
    df["relevant"] = df["text_norm"].fillna("").apply(lambda t: any(x and x in t for x in terms))
    out = df.groupby(["day", "platform"]).agg(posts=("text_norm", "size"), relevant=("relevant", "sum")).reset_index()
    return out.sort_values(["day", "platform"])


def hourly(camp: Campaign, day: str) -> pd.DataFrame:
    with connect(campaign_data(camp.name)) as con:
        df = pd.read_sql_query("SELECT platform, substr(posted_at,12,2) AS hour FROM posts WHERE substr(posted_at,1,10)=?", con, params=(day,))
    if df.empty:
        return df
    return df.groupby(["hour", "platform"]).size().rename("posts").reset_index()


def plot(camp: Campaign, out: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    df = daily(camp)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    if not df.empty:
        piv = df.pivot_table(index="day", columns="platform", values="relevant", aggfunc="sum").fillna(0)
        piv.index = pd.to_datetime(piv.index)
        start = min(piv.index.min(), pd.Timestamp(camp.since)) if camp.since else piv.index.min()
        end = max([piv.index.max()] + [pd.Timestamp(k.date) for k in camp.key_dates])
        piv = piv.reindex(pd.date_range(start, end, freq="D"), fill_value=0)
        bottom = None
        for i, col in enumerate(piv.columns):
            ax.bar(piv.index, piv[col], bottom=bottom, width=0.9, label=col, color=["#0F1923", "#0563C1", "#999999", "#CCCCCC"][i % 4])
            bottom = piv[col] if bottom is None else bottom + piv[col]
        ax.legend(frameon=False, fontsize=8)
    ymax = ax.get_ylim()[1] or 1
    for i, kd in enumerate(camp.key_dates):
        x = pd.Timestamp(kd.date)
        ax.axvline(x, color="#999999", linestyle="--", linewidth=0.8)
        ax.annotate(kd.label, (x, ymax), xytext=(3, -12 - 11 * (i % 4)), textcoords="offset points", fontsize=7, color="#0F1923", rotation=0)
    ax.set_title(f"{camp.title}: publicaciones relevantes por día", fontsize=11, color="#0F1923")
    ax.set_xlabel("")
    ax.set_ylabel("posts relevantes")
    ax.grid(alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out
