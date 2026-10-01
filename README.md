# faro

CLI de análisis OSINT de operaciones de influencia en redes. Capta fuentes públicas con cadena de
custodia (SHA-256 + timestamp), seudonimiza cuentas, detecta señales de coordinación (ráfagas,
duplicados de texto y vídeo, lotes de creación de cuentas), construye el grafo de amplificación y
genera un informe reproducible. Un proyecto **Don Z**.

**Primer caso: [Operación Faro](docs/informe-ceuta-2026.md)**, las convocatorias de entrada masiva
a Ceuta de julio a septiembre de 2026. El informe de cierre recoge qué se hizo, lo que se
encontró y sus límites.

Se organiza en **campañas**: un tema, su léxico multilingüe, sus semillas y sus fechas clave. La
primera es `ceuta-2026` (Operación Faro). La siguiente se crea con un comando.

## Instalación

```bash
uv venv .venv && uv pip install -e ".[dev]"
source .venv/bin/activate
faro --help
```

Requiere `ffmpeg` (frames de vídeo para pHash) y `yt-dlp` (lo instala el paquete).

## Dónde vive cada cosa

| Qué | Dónde | En el repo |
|---|---|---|
| Definición de campaña (léxico, semillas, fechas) | `campaigns/<nombre>/campaign.yml` | sí |
| Datos brutos, SQLite, frames | `~/.local/share/faro/<nombre>/` | **no** |
| Credenciales, sesión Telegram, clave HMAC | `~/.config/faro/` (0600) | **no** |
| Informes generados | `reports/<nombre>/` | no (gitignore) |

## Flujo

```bash
# 1. Campaña
faro campaign new ceuta-2026 -t "Operación Faro" --since 2026-07-01
faro campaign lexicon ceuta-2026 -l ar سبتة الحريق
faro campaign lexicon ceuta-2026 -l arabizi sebta l7rig
faro campaign keydate ceuta-2026 2026-09-23 "Convocatoria"

# 2. Credenciales (una vez). api_id/api_hash en https://my.telegram.org
faro auth telegram --phone +34XXXXXXXXX --api-id 12345 --api-hash abcdef…

# 3. Semillas
faro seeds discover -c ceuta-2026 --add          # busca canales públicos por el léxico
faro seeds add telegram canal1 canal2 -c ceuta-2026
faro seeds add tiktok @cuenta1 @cuenta2 -c ceuta-2026   # TikTok: cuentas o URLs, no hashtags
faro seeds add youtube "الحريق سبتة" -c ceuta-2026

# 4. Captación (repetible; solo inserta lo nuevo)
faro collect telegram -c ceuta-2026 --snowball   # añade canales descubiertos por forward/mención
faro collect all -c ceuta-2026                   # o scripts/round.sh ceuta-2026 en cron
faro status -c ceuta-2026

# 5. Análisis
faro analyze timeline -c ceuta-2026 --plot out.png
faro analyze bursts -c ceuta-2026 --kind text --window 10 --min-accounts 3
faro analyze dupes -c ceuta-2026            # texto (MinHash)
faro analyze dupes -c ceuta-2026 --media    # vídeo/imagen (pHash)
faro analyze graph -c ceuta-2026 --gexf grafo.gexf
faro analyze accounts -c ceuta-2026

# 6. Informe y custodia
faro report -c ceuta-2026
faro evidence verify -c ceuta-2026
faro evidence list telegram:123:456 -c ceuta-2026
```

## Principios que impone el código

- **Solo público.** Telethon solo entra en canales/grupos por username; yt-dlp solo en páginas públicas.
- **Seudónimos por defecto.** Todo lo que sale (tablas, informe, GEXF) lleva `te_a1b2…`, HMAC con
  clave local. El mapa real está en la tabla `identities`; `faro evidence reveal --yes` es la única
  puerta y avisa.
- **Evidencia inmutable.** Cada captura bruta se escribe una vez con su `.sha256`; `evidence verify`
  detecta alteraciones. Cada post apunta a su captura.
- **Coordinación exige ≥2 señales.** El informe lo dice en su propio texto.

## Limitaciones conocidas

- TikTok por hashtag no funciona vía yt-dlp (la API de app está rota); usar cuentas y URLs.
- Facebook y WhatsApp quedan fuera (sin acceso público programático).
- YouTube: la búsqueda no ordena por fecha; se filtra después por `posted_at`.
- El léxico en dariya necesita validación de un hablante nativo.

## Siguiente campaña

```bash
faro campaign new <nombre> -t "<título>" --since YYYY-MM-DD
```
y repetir el flujo. Nada de `ceuta-2026` está cableado en el código.

## Tests

```bash
pytest -q
```

## Módulo Tor / .onion (adaptado de Robin)

Flujo de [Robin](https://github.com/apurvsinghgautam/robin) (MIT) integrado como colector más:
buscar en motores .onion vía Tor, capturar páginas como evidencia con hash e ingerirlas como
posts `onion`. Capa LLM opcional para refinar consultas, filtrar y resumir: usa **Claude Code en modo
headless (`claude -p`)**, así que no hace falta API key, solo tener `claude` en el PATH. Modelo con
`FARO_CLAUDE_MODEL` (por defecto `opus`).

```bash
sudo pacman -S tor && sudo systemctl enable --now tor          # Tor en 127.0.0.1:9050
faro seeds import-robin /ruta/robin/search.py -c ceuta-2026   # motores .onion desde Robin
faro seeds add onion "sebta" "haraga" -c ceuta-2026
faro collect onion -c ceuta-2026                              # búsqueda + captura
faro collect onion -c ceuta-2026 --llm --summary              # capa LLM vía `claude -p` (suscripción de Claude Code)
```

Diferencias con Robin: sin interfaz web ni LangChain; evidencia inmutable por página; seudónimo por
host .onion; las páginas que no casan con el léxico se descartan salvo `--all`. El proxy se puede
cambiar con `FARO_TOR_PROXY` en `~/.config/faro/credentials.env`.

## Salida por proxy (OPSEC)

Toda la captación en clearnet (YouTube, TikTok, RDAP, Telegram) puede salir por un proxy fijando
`FARO_PROXY` en `~/.config/faro/credentials.env`, p. ej. `FARO_PROXY=socks5h://127.0.0.1:1080`
(un túnel `ssh -D 1080` o WireGuard hacia un VPS de la marca). Tor tiene su propio `FARO_TOR_PROXY`.
La capa LLM (`claude -p`) no pasa por el proxy: habla con Anthropic desde la sesión de Claude Code.

## Licencia

MIT. El módulo .onion adapta código de Robin (MIT); ver `NOTICE`.
