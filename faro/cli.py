"""faro — CLI. Uso: faro --help"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional
import json

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .paths import ensure_dirs, campaign_data, CONFIG_DIR, CAMPAIGNS_DIR
from .campaign import Campaign, Lexicon, Seeds, KeyDate
from . import settings

app = typer.Typer(help="Análisis OSINT de operaciones de influencia en redes. Proyecto Don Z.", no_args_is_help=True)
campaign_app = typer.Typer(help="Campañas: la unidad reutilizable (tema + léxico + semillas + fechas).", no_args_is_help=True)
seeds_app = typer.Typer(help="Semillas de captación de la campaña.", no_args_is_help=True)
collect_app = typer.Typer(help="Captación (solo fuentes públicas, con evidencia SHA-256).", no_args_is_help=True)
analyze_app = typer.Typer(help="Análisis: timeline, ráfagas, duplicados, grafo.", no_args_is_help=True)
auth_app = typer.Typer(help="Credenciales locales (~/.config/faro, 0600).", no_args_is_help=True)
evidence_app = typer.Typer(help="Cadena de custodia.", no_args_is_help=True)
app.add_typer(campaign_app, name="campaign")
app.add_typer(seeds_app, name="seeds")
app.add_typer(collect_app, name="collect")
app.add_typer(analyze_app, name="analyze")
app.add_typer(auth_app, name="auth")
app.add_typer(evidence_app, name="evidence")
con = Console()

CAMP_OPT = typer.Option(..., "--campaign", "-c", envvar="FARO_CAMPAIGN", help="Nombre de la campaña")


def _load(name: str) -> Campaign:
    try:
        return Campaign.load(name)
    except FileNotFoundError as e:
        con.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.callback()
def _init():
    ensure_dirs()


@app.command()
def version():
    """Versión."""
    con.print(f"faro {__version__}")


# ---------- campaign ----------
@campaign_app.command("new")
def campaign_new(name: str, title: str = typer.Option(..., "--title", "-t"),
                 description: str = typer.Option("", "--description", "-d"),
                 since: Optional[str] = typer.Option(None, help="Captar histórico desde YYYY-MM-DD")):
    """Crea una campaña vacía en campaigns/<name>/campaign.yml."""
    if name in Campaign.list_names():
        con.print(f"[red]Ya existe '{name}'.[/red]")
        raise typer.Exit(1)
    c = Campaign(name=name, title=title, description=description, created=date.today(),
                 since=date.fromisoformat(since) if since else None)
    p = c.save()
    con.print(f"[green]Campaña creada:[/green] {p}\nDatos irán a {campaign_data(name)} (fuera del repo).")


@campaign_app.command("list")
def campaign_list():
    """Lista campañas."""
    t = Table("Campaña", "Título", "Creada", "Seeds TG", "Hashtags TT", "Fechas clave")
    for n in Campaign.list_names():
        c = Campaign.load(n)
        t.add_row(n, c.title, str(c.created), str(len(c.seeds.telegram)), str(len(c.seeds.tiktok_hashtags)), str(len(c.key_dates)))
    con.print(t)


@campaign_app.command("show")
def campaign_show(name: str):
    """Muestra el YAML de la campaña."""
    con.print(Campaign.load(name).path.read_text())


@campaign_app.command("lexicon")
def campaign_lexicon(name: str, lang: str = typer.Option(..., "--lang", "-l", help="es | ar | arabizi | hashtags"),
                     terms: list[str] = typer.Argument(...)):
    """Añade términos al léxico: faro campaign lexicon ceuta-2026 -l ar سبتة الحريق"""
    c = _load(name)
    if lang == "hashtags":
        c.lexicon.hashtags = sorted(set(c.lexicon.hashtags) | set(terms))
    else:
        c.lexicon.terms.setdefault(lang, [])
        c.lexicon.terms[lang] = sorted(set(c.lexicon.terms[lang]) | set(terms))
    c.save()
    con.print(f"Léxico ({lang}): {len(terms)} términos añadidos. Total: {len(c.lexicon.all_terms())}.")


@campaign_app.command("keydate")
def campaign_keydate(name: str, day: str, label: str):
    """Añade una fecha clave (YYYY-MM-DD 'etiqueta')."""
    c = _load(name)
    c.key_dates.append(KeyDate(date=date.fromisoformat(day), label=label))
    c.key_dates.sort(key=lambda k: k.date)
    c.save()
    con.print(f"Fecha clave añadida: {day} — {label}")


# ---------- seeds ----------
@seeds_app.command("add")
def seeds_add(platform: str = typer.Argument(..., help="telegram | tiktok | youtube | domain"),
              values: list[str] = typer.Argument(...), campaign: str = CAMP_OPT):
    """Añade semillas: faro seeds add telegram canal1 canal2 -c ceuta-2026"""
    c = _load(campaign)
    field = {"telegram": "telegram", "tiktok": "tiktok_hashtags", "youtube": "youtube_queries", "domain": "domains",
             "onion": "onion_queries", "onion-engine": "onion_engines"}.get(platform)
    if not field:
        con.print("[red]Plataforma no válida.[/red]")
        raise typer.Exit(1)
    cur = getattr(c.seeds, field)
    setattr(c.seeds, field, sorted(set(cur) | set(values)))
    c.save()
    con.print(f"{platform}: {len(getattr(c.seeds, field))} semillas.")


@seeds_app.command("import-robin")
def seeds_import_robin(path: Path = typer.Argument(..., help="Ruta a search.py de Robin (github.com/apurvsinghgautam/robin)"),
                       campaign: str = CAMP_OPT):
    """Importa la lista de motores .onion de Robin como plantillas de buscador de la campaña."""
    import re
    c = _load(campaign)
    src = path.read_text(encoding="utf-8")
    tpls = [u.replace("{query}", "{q}") for u in re.findall(r'"url":\s*"([^"]+\.onion[^"]*\{query\}[^"]*)"', src)]
    if not tpls:
        con.print("[red]No se han encontrado motores en ese fichero.[/red]"); raise typer.Exit(1)
    c.seeds.onion_engines = sorted(set(c.seeds.onion_engines) | set(tpls))
    c.save()
    con.print(f"{len(tpls)} motores importados; total {len(c.seeds.onion_engines)}.")


@seeds_app.command("list")
def seeds_list(campaign: str = CAMP_OPT):
    c = _load(campaign)
    for k, v in c.seeds.model_dump().items():
        con.print(f"[bold]{k}[/bold] ({len(v)})")
        for x in v:
            con.print(f"  {x}")


@seeds_app.command("discover")
def seeds_discover(campaign: str = CAMP_OPT, limit: int = 20, add: bool = typer.Option(False, help="Añadir lo encontrado como semillas")):
    """Busca canales públicos de Telegram por los términos del léxico (snowball inicial)."""
    from .collect import telegram
    c = _load(campaign)
    found = telegram.search(c, limit=limit)
    t = Table("username", "título", "miembros", "término")
    for f in found:
        if "error" in f:
            con.print(f"[yellow]{f['term']}: {f['error']}[/yellow]")
            continue
        t.add_row(f["username"], f["title"] or "", str(f.get("participants") or ""), f["term"])
    con.print(t)
    if add:
        names = [f["username"] for f in found if "username" in f]
        c.seeds.telegram = sorted(set(c.seeds.telegram) | set(names))
        c.save()
        con.print(f"[green]{len(names)} canales añadidos como semilla.[/green]")


# ---------- auth ----------
@auth_app.command("telegram")
def auth_telegram(phone: str = typer.Option(..., "--phone", prompt="Teléfono de la cuenta dedicada (+34…)"),
                  api_id: str = typer.Option(None, "--api-id"), api_hash: str = typer.Option(None, "--api-hash")):
    """Guarda credenciales de la API de Telegram e inicia sesión (interactivo: pide el código)."""
    vals = {"FARO_TG_PHONE": phone}
    if api_id:
        vals["FARO_TG_API_ID"] = api_id
    if api_hash:
        vals["FARO_TG_API_HASH"] = api_hash
    p = settings.save(vals)
    con.print(f"Credenciales en {p}")
    from .collect import telegram
    who = telegram.login(phone)
    con.print(f"[green]Sesión Telegram iniciada como {who}.[/green] Sesión en {CONFIG_DIR}/telegram.session")


@auth_app.command("show")
def auth_show():
    """Muestra qué credenciales hay (sin valores)."""
    for k in settings.load():
        con.print(f"  {k} = ***")


# ---------- collect ----------
@collect_app.command("telegram")
def collect_telegram(campaign: str = CAMP_OPT, targets: Optional[list[str]] = typer.Argument(None),
                     limit: int = typer.Option(2000, help="Mensajes por canal"),
                     media: bool = typer.Option(False, help="Descargar vídeo/imagen de posts que casen con el léxico"),
                     snowball: bool = typer.Option(False, help="Añadir canales descubiertos por forward/mención como semillas")):
    """Capta canales públicos de Telegram (por defecto, las semillas de la campaña)."""
    from .collect import telegram
    c = _load(campaign)
    s = telegram.collect(c, targets or None, limit=limit, download_media=media)
    con.print(f"canales: {s['channels']}  posts nuevos: {s['new_posts']}  descubiertos: {len(s['discovered'])}")
    for t, e in s.get("errors", []):
        con.print(f"  [yellow]{t}: {e}[/yellow]")
    if s["discovered"]:
        con.print("Descubiertos: " + ", ".join(s["discovered"][:40]) + (" …" if len(s["discovered"]) > 40 else ""))
        if snowball:
            new = [d for d in s["discovered"] if not d.startswith("id:")]
            c.seeds.telegram = sorted(set(c.seeds.telegram) | set(new))
            c.save()
            con.print(f"[green]{len(new)} añadidos como semilla.[/green]")


@collect_app.command("tiktok")
def collect_tiktok(campaign: str = CAMP_OPT, targets: Optional[list[str]] = typer.Argument(None),
                   limit: int = 100, media: bool = False):
    """Capta TikTok por hashtag (#x), usuario (@x) o URL vía yt-dlp."""
    from .collect import ytdlp
    c = _load(campaign)
    s = ytdlp.tiktok(c, targets or None, limit=limit, media=media)
    con.print(f"targets: {s['targets']}  posts nuevos: {s['new_posts']}")
    for t, e in s["errors"]:
        con.print(f"  [yellow]{t}: {e}[/yellow]")


@collect_app.command("youtube")
def collect_youtube(campaign: str = CAMP_OPT, queries: Optional[list[str]] = typer.Argument(None),
                    limit: int = 50, media: bool = False):
    """Busca en YouTube por los términos semilla (ordenado por fecha)."""
    from .collect import ytdlp
    c = _load(campaign)
    s = ytdlp.youtube(c, queries or None, limit=limit, media=media)
    con.print(f"queries: {s['targets']}  posts nuevos: {s['new_posts']}")
    for t, e in s["errors"]:
        con.print(f"  [yellow]{t}: {e}[/yellow]")


@collect_app.command("domains")
def collect_domains(campaign: str = CAMP_OPT, all: bool = typer.Option(False, "--all", help="Reenriquecer todos")):
    """Enriquece dominios vistos (RDAP + DNS)."""
    from .collect import domains
    c = _load(campaign)
    s = domains.enrich(c, only_new=not all)
    con.print(f"enriquecidos: {s['enriched']}  omitidos (plataformas): {s['skipped']}")


@collect_app.command("onion")
def collect_onion(campaign: str = CAMP_OPT, queries: Optional[list[str]] = typer.Argument(None, help="Consultas (por defecto seeds.onion_queries)"),
                  scrape_limit: int = typer.Option(30, help="Páginas a capturar"),
                  all_pages: bool = typer.Option(False, "--all", help="Capturar también las que no casan con el léxico"),
                  llm: bool = typer.Option(False, help="Refinar consultas y filtrar resultados con Claude (FARO_ANTHROPIC_API_KEY)"),
                  summary: bool = typer.Option(False, help="Resumen analítico con Claude de lo capturado (implica --llm)")):
    """Busca en los motores .onion de la campaña vía Tor, captura páginas como evidencia e ingiere posts `onion` (flujo tipo Robin)."""
    from .collect import onion
    from . import llm as llmmod
    c = _load(campaign)
    if not c.seeds.onion_engines:
        con.print("[red]La campaña no tiene motores .onion.[/red] Añádelos con: faro seeds import-robin <ruta>/search.py -c campaña")
        raise typer.Exit(1)
    ok, ip = onion.tor_ok()
    if not ok:
        con.print(f"[red]Tor no disponible en {onion.tor_proxy()} ({ip}).[/red] Instala y arranca Tor: sudo pacman -S tor && sudo systemctl enable --now tor")
        raise typer.Exit(1)
    con.print(f"Tor OK (salida {ip}) · {len(c.seeds.onion_engines)} motores")
    qs = list(queries or c.seeds.onion_queries)
    use_llm = llm or summary
    if use_llm and not llmmod.available():
        con.print("[yellow]Sin FARO_ANTHROPIC_API_KEY: sigo sin capa LLM.[/yellow]")
        use_llm = False
    if use_llm:
        qs = sorted(set(qs) | set(llmmod.refine_queries(c.title + ". " + c.description, c.lexicon.all_terms())))
        con.print("Consultas refinadas: " + " · ".join(qs))
    if not qs:
        con.print('[red]Sin consultas. Usa: faro seeds add onion "..." -c campaña[/red]')
        raise typer.Exit(1)
    hits, st = onion.search(c, qs)
    con.print(f"motores ok/fallo: {st['engines_ok']}/{st['engines_fail']} · hits únicos: {len(hits)}")
    if use_llm and hits:
        hits = llmmod.filter_relevant(c.title + ". " + c.description, hits)
        con.print(f"tras filtro LLM: {len(hits)}")
    t = Table("motores", "título", "enlace")
    for h in hits[:25]:
        t.add_row(str(len(h["engines"])), h["title"][:60], h["link"][:60])
    con.print(t)
    s = onion.scrape(c, hits, limit=scrape_limit, only_relevant=not all_pages)
    con.print(f"capturadas {s['fetched']} · ingeridas {s['ingested']} · irrelevantes {s['irrelevant']} · errores {s['errors']}")
    if summary and use_llm:
        from .db import connect
        with connect(campaign_data(c.name)) as db:
            pages = [{"title": (r[0] or "")[:120], "url": r[1], "text": r[0] or ""}
                     for r in db.execute("SELECT text, url FROM posts WHERE platform='onion' ORDER BY captured_at DESC LIMIT 25")]
        out = Path.cwd() / "reports" / c.name / "onion-summary.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# Resumen asistido (Tor) — {c.title}\n\n*Lectura asistida por modelo; no sustituye la evidencia.*\n\n"
                       + llmmod.summarize(c.title, pages), encoding="utf-8")
        con.print(f"[green]Resumen:[/green] {out}")


@collect_app.command("all")
def collect_all(campaign: str = CAMP_OPT, media: bool = False):
    """Ronda completa: telegram + tiktok + youtube + dominios. Pensado para cron."""
    from .collect import telegram, ytdlp, domains
    c = _load(campaign)
    out = {}
    if c.seeds.youtube_queries:
        out["youtube"] = ytdlp.youtube(c, media=media)
    if c.seeds.tiktok_hashtags:
        out["tiktok"] = ytdlp.tiktok(c, media=media)
    if c.seeds.telegram:
        creds = settings.load()
        if creds.get("FARO_TG_API_ID") and creds.get("FARO_TG_API_HASH"):
            try:
                out["telegram"] = telegram.collect(c, download_media=media)
            except Exception as e:
                out["telegram"] = {"error": repr(e)[:200]}
        else:
            out["telegram"] = {"skipped": "sin credenciales (faro auth telegram)"}
    out["domains"] = domains.enrich(c)
    for k, v in out.items():
        con.print(f"[bold]{k}[/bold]: " + ", ".join(f"{a}={b if not isinstance(b, list) else len(b)}" for a, b in v.items()))


# ---------- analyze ----------
@analyze_app.command("timeline")
def analyze_timeline(campaign: str = CAMP_OPT, plot: Optional[Path] = typer.Option(None, help="Guardar PNG")):
    """Posts por día y plataforma (y cuántos casan con el léxico)."""
    from .analyze import timeline
    c = _load(campaign)
    df = timeline.daily(c)
    if df.empty:
        con.print("Sin datos.")
        raise typer.Exit()
    t = Table("día", "plataforma", "posts", "relevantes")
    for r in df.itertuples():
        t.add_row(r.day, r.platform, str(r.posts), str(int(r.relevant)))
    con.print(t)
    if plot:
        con.print(f"Figura: {timeline.plot(c, plot)}")


@analyze_app.command("bursts")
def analyze_bursts(campaign: str = CAMP_OPT, window: int = 10, min_accounts: int = 3, kind: str = typer.Option("text", help="text | url | media")):
    """Ráfagas: mismo contenido por ≥N cuentas en W minutos."""
    from .analyze import bursts
    c = _load(campaign)
    fn = {"text": bursts.text_bursts, "url": bursts.url_bursts, "media": bursts.media_bursts}[kind]
    res = fn(c, window_min=window, min_accounts=min_accounts)
    if not res:
        con.print("Sin ráfagas.")
        raise typer.Exit()
    t = Table("inicio", "cuentas", "posts", "contenido")
    for b in res[:50]:
        t.add_row(b["start"], str(b["n_accounts"]), str(b["n_posts"]), b["key"][:90])
    con.print(t)


@analyze_app.command("dupes")
def analyze_dupes(campaign: str = CAMP_OPT, threshold: float = 0.7, media: bool = typer.Option(False, help="pHash de media en vez de texto")):
    """Near-duplicates entre cuentas distintas; escribe aristas dup_* en el grafo."""
    from .analyze import dupes
    c = _load(campaign)
    if media:
        n = dupes.hash_media(c)
        con.print(f"pHash calculados: {n}")
        res = dupes.media_near_dupes(c)
        key = "distance"
    else:
        res = dupes.text_near_dupes(c, threshold=threshold)
        key = "sim"
    con.print(f"{len(res)} pares.")
    t = Table("a", "b", "cuenta a", "cuenta b", key)
    for p in res[:40]:
        t.add_row(p["a"], p["b"], p["acc_a"], p["acc_b"], str(p[key]))
    con.print(t)


@analyze_app.command("graph")
def analyze_graph(campaign: str = CAMP_OPT, top: int = 25, gexf: Optional[Path] = typer.Option(None, help="Exportar GEXF para Gephi")):
    """Grafo de amplificación: ranking por PageRank y comunidades."""
    from .analyze import graph
    c = _load(campaign)
    G = graph.build(c)
    con.print(f"{G.number_of_nodes()} nodos, {G.number_of_edges()} aristas")
    t = Table("cuenta", "plataforma", "pagerank", "amplificada por", "amplifica", "posts", "comunidad", "semilla")
    for r in graph.rank(G, top):
        t.add_row(r["account"], r["platform"] or "", str(r["pagerank"]), str(r["amplified_by"]), str(r["amplifies"]), str(r["posts"]), str(r["community"]), "●" if r["seed"] else "")
    con.print(t)
    if gexf:
        con.print(f"GEXF: {graph.export(G, gexf)}")


@analyze_app.command("evidence")
def analyze_evidence(campaign: str = CAMP_OPT, out: Optional[Path] = typer.Option(None, help="Directorio de salida (por defecto reports/<campaña>/graph/)"),
                     no_posts: bool = typer.Option(False, "--no-posts", help="Solo cuentas/entidades/términos/fechas (grafo compacto)"),
                     max_posts: int = typer.Option(600, help="Máximo de nodos de publicación")):
    """Grafo de evidencia tipado con procedencia: GEXF + GraphML + JSON + visor HTML autónomo."""
    from .analyze import evidence
    c = _load(campaign)
    G = evidence.build(c, include_posts=not no_posts, max_posts=max_posts)
    out = out or (Path.cwd() / "reports" / c.name / "graph")
    paths = evidence.export(G, out)
    s = evidence.summary(G)
    con.print(f"{s['nodes']} nodos, {s['edges']} aristas")
    con.print("  nodos: " + ", ".join(f"{k}={v}" for k, v in sorted(s["node_types"].items())))
    con.print("  aristas: " + ", ".join(f"{k}={v}" for k, v in sorted(s["edge_types"].items())))
    for k, p in paths.items():
        con.print(f"  {k:8} {p}")


@analyze_app.command("accounts")
def analyze_accounts(campaign: str = CAMP_OPT, top: int = 30):
    """Cuentas por volumen, con fecha de creación y seguidores (para detectar lotes)."""
    from .analyze import bursts
    c = _load(campaign)
    from .db import connect
    with connect(campaign_data(c.name)) as db:
        rows = db.execute("""SELECT a.pseudo, a.platform, a.kind, a.created_at, a.followers, a.seed, COUNT(p.id) n
                             FROM accounts a LEFT JOIN posts p ON p.account=a.pseudo GROUP BY a.pseudo ORDER BY n DESC LIMIT ?""", (top,)).fetchall()
    t = Table("cuenta", "plataforma", "tipo", "creada", "seguidores", "semilla", "posts")
    for r in rows:
        t.add_row(r[0], r[1], r[2] or "", (r[3] or "")[:10], str(r[4] or ""), "●" if r[5] else "", str(r[6]))
    con.print(t)
    cl = bursts.account_creation_clusters(c)
    if not cl.empty:
        con.print("[bold]Lotes de creación:[/bold]")
        con.print(cl.to_string(index=False))


# ---------- report / status / evidence ----------
@app.command()
def report(campaign: str = CAMP_OPT, out: Optional[Path] = typer.Option(None, help="Directorio de salida (por defecto reports/<campaña>/)"),
           lexicon_validated: bool = typer.Option(False, help="Marcar el léxico como validado por hablante nativo")):
    """Genera informe Markdown + figuras + GEXF con solo seudónimos."""
    from . import report as rep
    c = _load(campaign)
    out = out or (Path.cwd() / "reports" / c.name)
    p = rep.build(c, out, lexicon_validated=lexicon_validated)
    con.print(f"[green]Informe:[/green] {p}")


@app.command()
def site(campaign: str = CAMP_OPT, out: Optional[Path] = typer.Option(None, help="Directorio de salida (por defecto reports/<campaña>/site/)")):
    """Sitio estático completo de la campaña: resumen con hallazgos, cronología, grafo, informe y Tor. Sin CDN."""
    from . import site as sitemod
    c = _load(campaign)
    out = out or (Path.cwd() / "reports" / c.name / "site")
    p = sitemod.build(c, out)
    con.print(f"[green]Sitio:[/green] {p}")


@app.command()
def timeline(campaign: str = CAMP_OPT, out: Optional[Path] = typer.Option(None, help="Directorio de salida (por defecto reports/<campaña>/)")):
    """Vista de línea temporal: volumen diario + hitos + tramos (campaigns/<c>/timeline.yml) → HTML y Markdown."""
    from . import report_timeline
    c = _load(campaign)
    out = out or (Path.cwd() / "reports" / c.name)
    try:
        paths = report_timeline.build(c, out)
    except FileNotFoundError as e:
        con.print(f"[red]{e}[/red]"); raise typer.Exit(1)
    for k, p in paths.items():
        con.print(f"  {k:5} {p}")


@app.command()
def status(campaign: str = CAMP_OPT):
    """Estado de la campaña: corpus, últimas rondas, errores."""
    from .db import connect
    c = _load(campaign)
    data = campaign_data(c.name)
    with connect(data) as db:
        t = Table("plataforma", "cuentas", "posts", "primero", "último")
        for r in db.execute("SELECT platform, COUNT(DISTINCT account), COUNT(*), MIN(posted_at), MAX(posted_at) FROM posts GROUP BY platform"):
            t.add_row(r[0], str(r[1]), str(r[2]), (r[3] or "")[:16], (r[4] or "")[:16])
        con.print(t)
        con.print("[bold]Últimas rondas[/bold]")
        for r in db.execute("SELECT started, platform, target, n_new, status, error FROM runs ORDER BY id DESC LIMIT 10"):
            con.print(f"  {r[0][:16]} {r[1]:9} {r[2][:30]:30} +{r[3] or 0:<5} {r[4]} {('· ' + (r[5] or '')[:60]) if r[5] else ''}")
    con.print(f"Datos: {data}  ·  Campaña: {c.path}")


@evidence_app.command("verify")
def evidence_verify(campaign: str = CAMP_OPT):
    """Recalcula SHA-256 de todas las capturas brutas."""
    from .evidence import verify_raw
    c = _load(campaign)
    res = verify_raw(campaign_data(c.name))
    bad = [p for p, ok in res if not ok]
    con.print(f"{len(res) - len(bad)}/{len(res)} verifican.")
    for p in bad:
        con.print(f"  [red]ALTERADO/FALTA: {p}[/red]")
    raise typer.Exit(1 if bad else 0)


@evidence_app.command("list")
def evidence_list(campaign: str = CAMP_OPT, ids: list[str] = typer.Argument(...)):
    """Muestra evidencia de publicaciones concretas (ID, URL, captura y hash)."""
    from .db import connect
    c = _load(campaign)
    with connect(campaign_data(c.name)) as db:
        for pid in ids:
            r = db.execute("SELECT id, account, posted_at, captured_at, url, raw_path, raw_sha256 FROM posts WHERE id=?", (pid,)).fetchone()
            con.print(json.dumps(dict(r) if r else {"id": pid, "error": "no existe"}, ensure_ascii=False, indent=2))


@evidence_app.command("reveal")
def evidence_reveal(campaign: str = CAMP_OPT, pseudos: list[str] = typer.Argument(...),
                    yes: bool = typer.Option(False, "--yes", help="Confirmo que esto no va a ningún entregable")):
    """Resuelve seudónimo → handle real. SOLO para uso interno; nunca en entregables."""
    if not yes:
        con.print("[red]Añade --yes para confirmar que el resultado no sale de este disco.[/red]")
        raise typer.Exit(1)
    from .db import connect
    c = _load(campaign)
    with connect(campaign_data(c.name)) as db:
        for ps in pseudos:
            r = db.execute("SELECT platform, handle FROM identities WHERE pseudo=?", (ps,)).fetchone()
            con.print(f"{ps} → {r[0]}:{r[1]}" if r else f"{ps} → (desconocido)")


if __name__ == "__main__":
    app()
