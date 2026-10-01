"""Vista de línea temporal: volumen diario del corpus + hitos + tramos con lectura analítica.
Genera un HTML autónomo (sin dependencias externas) y un Markdown."""
from __future__ import annotations
import html
import json
from pathlib import Path
import yaml
from .campaign import Campaign
from .paths import campaign_dir
from .analyze import timeline as tl
from .evidence import utcnow
from . import __version__

KIND_ES = {"legal": "legal", "incitement": "incitación", "coordination": "coordinación", "narrative": "narrativa",
           "event": "hecho", "response": "respuesta", "analysis": "análisis"}


def load(camp: Campaign) -> dict:
    p = campaign_dir(camp.name) / "timeline.yml"
    if not p.exists():
        raise FileNotFoundError(f"No hay {p}. Define hitos y tramos ahí.")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _daily_series(camp: Campaign) -> list[dict]:
    df = tl.daily(camp)
    if df.empty:
        return []
    piv = df.pivot_table(index="day", columns="platform", values="relevant", aggfunc="sum").fillna(0)
    return [{"day": d, **{c: int(piv.loc[d, c]) for c in piv.columns}} for d in piv.index]


def build(camp: Campaign, out_dir: Path) -> dict[str, Path]:
    spec = load(camp)
    series = _daily_series(camp)
    # Volumen por tramo desde la serie
    for t in spec.get("tramos", []):
        a, b = str(t["from"]), str(t["to"])
        rows = [r for r in series if a <= r["day"] <= b]
        t["volume"] = {k: sum(r.get(k, 0) for r in rows) for k in {k for r in rows for k in r if k != "day"}}
        t["days"] = len(rows)
    data = {"campaign": camp.title, "name": camp.name, "generated": utcnow(), "version": __version__,
            "series": series, "milestones": sorted(spec.get("milestones", []), key=lambda m: str(m["date"])),
            "tramos": spec.get("tramos", [])}
    for m in data["milestones"]:
        m["date"] = str(m["date"]); m["kind_es"] = KIND_ES.get(m.get("kind", ""), m.get("kind", ""))
    for t in data["tramos"]:
        t["from"] = str(t["from"]); t["to"] = str(t["to"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl = (Path(__file__).parent / "timeline_viewer.html").read_text(encoding="utf-8")
    h = out_dir / "timeline.html"
    h.write_text(tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")), encoding="utf-8")
    md = out_dir / "timeline.md"
    md.write_text(_markdown(data), encoding="utf-8")
    return {"html": h, "md": md}


def _markdown(d: dict) -> str:
    out = [f"# {d['campaign']} — línea temporal\n", f"*faro {d['version']} · {d['generated']} · campaña `{d['name']}` · Don Z*\n", "## Hitos\n",
           "| Fecha | Tipo | Hito | Fuente |", "|---|---|---|---|"]
    for m in d["milestones"]:
        out.append(f"| {m['date']} | {m['kind_es']} | {m['label']} | {m.get('source', '')} |")
    out.append("\n## Tramos: qué se incita\n")
    for t in d["tramos"]:
        vol = ", ".join(f"{k} {v}" for k, v in sorted(t["volume"].items())) or "sin corpus"
        out.append(f"### {t['from']} → {t['to']} · {t['title']}\n")
        out.append(f"*Corpus relevante: {vol}. Fuentes: {', '.join(t.get('sources', []))}.*\n")
        out.append(t["incitement"].strip() + "\n")
        if t.get("signals"):
            out.append("Señales: " + " · ".join(t["signals"]) + "\n")
    return "\n".join(out)
