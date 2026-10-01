"""Importa los resultados de un escaneo de SpiderFoot como evidencia de la campaña.

SpiderFoot sigue siendo una instalación aparte; faro solo guarda su exportación con SHA-256 y
la normaliza en la tabla `external`. Formatos admitidos:
- JSON de la interfaz web (Exportar → JSON): data, event_type, module, source_data, last_seen…
- JSON de la CLI (`sf.py -o json`): generated, type, data, module, source
- CSV de la interfaz web: [Scan Name,] Updated, Type, Module, Source, F/P, Data
- CSV de la CLI (`sf.py -o csv`): Source, Type, [Source Data,] Data
"""
from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from ..campaign import Campaign
from ..db import connect
from ..evidence import store_raw, utcnow
from ..paths import campaign_data


def _rows_json(items: list[dict]) -> list[dict]:
    out = []
    for e in items:
        if "event_type" in e:          # exportación web
            kind, src, seen = e.get("event_type"), e.get("source_data"), e.get("last_seen")
        else:                          # CLI
            kind, src = e.get("type"), e.get("source")
            g = e.get("generated")
            seen = datetime.fromtimestamp(g, timezone.utc).isoformat(timespec="seconds") if g else None
        out.append({"kind": kind, "module": e.get("module"), "data": e.get("data"), "source_data": src,
                    "seen": seen, "false_positive": int(e.get("false_positive") or 0)})
    return out


def _rows_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    head, body = rows[0], rows[1:]
    out = []
    if "Module" in head:               # exportación web: [Scan Name,] Updated, Type, Module, Source, F/P, Data
        for r in body:
            d = dict(zip(head, r))
            out.append({"kind": d.get("Type"), "module": d.get("Module"), "data": d.get("Data"),
                        "source_data": d.get("Source"), "seen": d.get("Updated"),
                        "false_positive": int(d.get("F/P") or 0)})
        return out
    # CLI (`sf.py -o csv`): Source(=módulo), Type, [Source Data,] Data. La cabecera a veces omite
    # "Source Data" aunque las filas la traen, así que se decide por fila.
    for r in body:
        if len(r) >= 4:
            module, kind, src, data = r[0], r[1], r[2], ",".join(r[3:])
        elif len(r) == 3:
            module, kind, data, src = r[0], r[1], r[2], None
        else:
            continue
        out.append({"kind": kind, "module": module, "data": data, "source_data": src, "seen": None,
                    "false_positive": 0})
    return out


def parse(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if text.startswith("["):
        return _rows_json(json.loads(text))
    first = text.splitlines()[0] if text else ""
    if "Type" in first and "Data" in first:
        return _rows_csv(text)
    raise ValueError("Formato no reconocido: usa la exportación JSON/CSV de SpiderFoot o `sf.py -o json`.")


def ingest(camp: Campaign, path: Path) -> dict:
    raw = path.read_bytes()
    rows = [r for r in parse(raw) if r["kind"] and r["kind"] != "ROOT" and r["data"]]
    data, now = campaign_data(camp.name), utcnow()
    rp, digest = store_raw(data, "spiderfoot", path.name, raw)
    stats = {"rows": len(rows), "new": 0, "types": {}}
    with connect(data) as con:
        for r in rows:
            cur = con.execute("""INSERT OR IGNORE INTO external(tool, kind, module, data, source_data, seen,
                                 false_positive, imported_at, raw_path, raw_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              ("spiderfoot", r["kind"], r["module"], r["data"], r["source_data"], r["seen"],
                               r["false_positive"], now, str(rp), digest))
            stats["new"] += cur.rowcount
            stats["types"][r["kind"]] = stats["types"].get(r["kind"], 0) + 1
    return stats
