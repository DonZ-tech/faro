"""Capa LLM opcional a través de Claude Code en modo headless (`claude -p`).

Usa la sesión/suscripción de Claude Code del equipo: no hace falta API key. Cada llamada es un
proceso `claude -p` sin persistencia de sesión y sin herramientas (solo texto), con salida
estructurada por JSON Schema cuando hace falta. Si `claude` no está en el PATH, la capa se
desactiva y faro sigue funcionando sin ella. Lo que sale de aquí se etiqueta "lectura asistida"
y nunca sustituye a la evidencia.
"""
from __future__ import annotations
import json
import shutil
import subprocess
from typing import Any
from pydantic import BaseModel, Field
from .settings import load as load_creds

DEFAULT_MODEL = "opus"


def available() -> bool:
    return shutil.which(load_creds().get("FARO_CLAUDE_BIN", "claude")) is not None


def _run(prompt: str, system: str, schema: dict | None = None, timeout: int = 600) -> Any:
    """Ejecuta `claude -p`. Con schema devuelve el objeto validado; sin él, el texto de respuesta."""
    creds = load_creds()
    cmd = [creds.get("FARO_CLAUDE_BIN", "claude"), "-p", "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--model", creds.get("FARO_CLAUDE_MODEL", DEFAULT_MODEL), "--system-prompt", system]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p falló ({r.returncode}): {r.stderr.strip()[-400:]}")
    # El shim de mise puede anteponer una línea informativa antes del JSON: saltar hasta el primer '{'.
    out = r.stdout
    start = out.find("{")
    try:
        env = json.loads(out[start:]) if start >= 0 else None
    except json.JSONDecodeError:
        env = None
    if env is None:
        return out.strip()
    if isinstance(env, dict):
        if env.get("is_error"):
            raise RuntimeError(f"claude -p error: {str(env.get('result'))[:400]}")
        if schema:
            so = env.get("structured_output")
            if so is not None:
                return so
            try:
                return json.loads(env.get("result", ""))
            except Exception:
                raise RuntimeError("claude -p no devolvió salida estructurada")
        return env.get("result", "")
    return r.stdout.strip()


class Queries(BaseModel):
    queries: list[str] = Field(description="Consultas cortas para buscadores .onion, en varios idiomas")


class Relevance(BaseModel):
    relevant_indexes: list[int] = Field(description="Índices (0-based) de los resultados relevantes para la investigación")
    reasons: list[str] = Field(description="Un motivo breve por índice relevante, en el mismo orden")


def refine_queries(topic: str, lexicon: list[str], n: int = 8) -> list[str]:
    """Convierte tema y léxico en consultas cortas para buscadores .onion (primitivos: sin operadores)."""
    out = _run(
        f"Tema: {topic}\nLéxico: {', '.join(lexicon[:60])}\nDevuelve {n} consultas.",
        system="Eres analista OSINT. Generas consultas para motores de búsqueda de la red Tor, que son primitivos: "
               "2-4 palabras, sin comillas ni operadores, en los idiomas del léxico (español, árabe, francés, inglés). "
               "Cubre foros donde se anuncie transporte o paso de personas, venta de datos filtrados relacionados y "
               "canales de propaganda. No inventes nombres de sitios.",
        schema=Queries.model_json_schema())
    return Queries.model_validate(out).queries[:n]


def filter_relevant(topic: str, hits: list[dict]) -> list[dict]:
    """Marca qué resultados merecen captura. Devuelve la sublista con campo `reason`."""
    if not hits:
        return []
    listing = "\n".join(f"{i}. {h['title'][:120]} — {h['link'][:70]}" for i, h in enumerate(hits[:120]))
    out = _run(
        f"Investigación: {topic}\n\nResultados:\n{listing}",
        system="Eres analista OSINT. Se te da una lista de resultados de buscadores Tor. Marca solo los que puedan aportar a la "
               "investigación indicada. Descarta mercados ilegales, pornografía, estafas genéricas y directorios sin relación.",
        schema=Relevance.model_json_schema())
    rel = Relevance.model_validate(out)
    res = []
    for i, why in zip(rel.relevant_indexes, rel.reasons):
        if 0 <= i < len(hits):
            h = dict(hits[i])
            h["reason"] = why
            res.append(h)
    return res


def summarize(topic: str, pages: list[dict]) -> str:
    """Resumen analítico de páginas capturadas. Cita la URL de la que sale cada afirmación."""
    corpus = "\n\n".join(f"### {p['title']}\nURL: {p['url']}\n{p['text'][:6000]}" for p in pages[:25])
    return _run(
        f"Investigación: {topic}\n\n{corpus}",
        system="Eres analista OSINT. Resume en español y en Markdown lo que estas páginas aportan a la investigación. "
               "Separa: hallazgos con evidencia (cita la URL exacta tras cada afirmación), indicios débiles y ruido. "
               "No afirmes nada que no esté en el texto. Marca explícitamente lo que no se puede verificar.")
