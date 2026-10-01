# faro

Herramienta OSINT de línea de comandos con cadena de custodia. Cubre dos frentes:

- **Operaciones de influencia en redes.** Capta fuentes públicas (YouTube, TikTok, Telegram, .onion),
  seudonimiza las cuentas, detecta señales de coordinación (ráfagas, duplicados de texto y vídeo,
  cuentas creadas en lote), construye el grafo de amplificación y genera un informe reproducible.
- **Filtraciones de datos.** Brechas conocidas (Have I Been Pwned), código expuesto en GitHub y
  búsquedas avanzadas (dorks) para revisar a mano. Importa además los escaneos de
  [SpiderFoot](https://github.com/smicallef/spiderfoot).

Todo lo que capta se guarda una vez, con su SHA-256, y se puede verificar después. Un proyecto **Don Z**.

**Caso publicado:** [Operación Faro](https://github.com/DonZ-tech/operacion-faro), las
convocatorias de entrada masiva a Ceuta de julio a septiembre de 2026.

## Instalación

```bash
uv venv .venv && uv pip install -e ".[dev]"
source .venv/bin/activate
faro --help
```

Requiere `ffmpeg` (frames de vídeo para pHash) y `yt-dlp` (lo instala el paquete).

## Campañas

Todo se organiza en **campañas**: un tema con su léxico multilingüe, sus semillas, sus fechas clave
y sus dominios. Una campaña es un fichero YAML que puede ir a un repositorio; los datos que capta
no van nunca.

| Qué | Dónde | ¿Va al repositorio? |
|---|---|---|
| Definición de campaña | `campaigns/<nombre>/campaign.yml` (o `FARO_CAMPAIGNS_DIR`) | sí |
| Datos brutos, SQLite, frames | `~/.local/share/faro/<nombre>/` | **no** |
| Credenciales, sesión de Telegram, clave HMAC | `~/.config/faro/` (0600) | **no** |
| Informes generados | `reports/<nombre>/` | no |

```bash
faro campaign new mi-campana -t "Título" --since 2026-07-01
faro campaign lexicon mi-campana -l es "entrada masiva" frontera
faro campaign keydate mi-campana 2026-09-23 "Convocatoria"
faro seeds add domain ejemplo.es -c mi-campana
```

## Operaciones de influencia

```bash
# Credenciales de Telegram (una vez). api_id/api_hash en https://my.telegram.org
faro auth telegram --phone +34XXXXXXXXX --api-id 12345 --api-hash abcdef…

# Semillas
faro seeds discover -c mi-campana --add                 # canales públicos de Telegram por el léxico
faro seeds add telegram canal1 canal2 -c mi-campana
faro seeds add tiktok @cuenta1 @cuenta2 -c mi-campana   # TikTok: cuentas o URLs, no hashtags
faro seeds add youtube "consulta" -c mi-campana

# Captación (repetible; solo inserta lo nuevo)
faro collect telegram -c mi-campana --snowball   # añade canales descubiertos por reenvío y mención
faro collect all -c mi-campana                   # o scripts/round.sh mi-campana en cron
faro status -c mi-campana

# Análisis
faro analyze timeline -c mi-campana --plot out.png
faro analyze bursts -c mi-campana --kind text --window 10 --min-accounts 3
faro analyze dupes -c mi-campana            # texto (MinHash)
faro analyze dupes -c mi-campana --media    # vídeo e imagen (pHash)
faro analyze graph -c mi-campana --gexf grafo.gexf
faro analyze accounts -c mi-campana

# Informe
faro report -c mi-campana
faro timeline -c mi-campana     # usa campaigns/<c>/timeline.yml si existe
faro site -c mi-campana         # sitio estático sin CDN
```

### Tor y .onion (adaptado de Robin)

Flujo de [Robin](https://github.com/apurvsinghgautam/robin) integrado como un colector más:
buscar en motores .onion a través de Tor, capturar las páginas como evidencia e ingerirlas como
publicaciones `onion`.

```bash
sudo pacman -S tor && sudo systemctl enable --now tor          # Tor en 127.0.0.1:9050
faro seeds import-robin /ruta/robin/search.py -c mi-campana   # motores .onion desde Robin
faro seeds add onion "consulta" -c mi-campana
faro collect onion -c mi-campana
faro collect onion -c mi-campana --llm --summary
```

La capa LLM es opcional: refina consultas, filtra resultados y resume. Funciona con **Claude Code en
modo headless (`claude -p`)**, así que basta con tener `claude` en el PATH. El modelo se elige con
`FARO_CLAUDE_MODEL`.

## Filtraciones

```bash
faro leaks breaches -c mi-campana            # brechas sufridas por cada dominio (gratis, sin clave)
faro leaks hibp -c mi-campana -f emails.txt  # brechas de cada email (FARO_HIBP_API_KEY)
faro leaks github -c mi-campana              # código público que menciona el dominio (FARO_GITHUB_TOKEN)
faro leaks dorks -c mi-campana --open        # búsquedas avanzadas para revisar a mano
faro leaks list -c mi-campana                # hallazgos, primero los que exponen credenciales
faro leaks list -c mi-campana --since 2026-10-01
```

- Los **emails** no se guardan en la campaña: se pasan por argumento o fichero y se seudonimizan.
  `faro leaks list --reveal` los muestra, solo para uso interno.
- `leaks github` lanza una búsqueda por indicio (`password`, `smtp`, `.env`, `.sql`): la API de
  búsqueda de código no admite `OR` entre cualificadores. Marca un fichero como sensible cuando
  contiene una credencial asignada con valor, no cuando solo aparece la palabra.
- Los **dorks** se generan pero no se lanzan: los buscadores no permiten automatizarlos.
- Vigilancia de pastes: ni Telegraph ni GitHub Gists tienen API de búsqueda. Queda en los dorks.

Las claves van en `~/.config/faro/credentials.env` o como variables de entorno:

```
FARO_HIBP_API_KEY=…      # https://haveibeenpwned.com/API/Key
FARO_GITHUB_TOKEN=…      # token sin permisos: solo hace falta para buscar
```

## Importar SpiderFoot

SpiderFoot es una instalación aparte. faro guarda su exportación como evidencia y la normaliza en
la tabla `external`, así que se puede cruzar con lo demás de la campaña.

```bash
python sf.py -s ejemplo.es -m sfp_dnsresolve,sfp_whois -o json -q > escaneo.json
faro import spiderfoot escaneo.json -c mi-campana
```

Admite el JSON y el CSV de la CLI (`-o json`, `-o csv`) y las exportaciones JSON y CSV de la
interfaz web.

## Custodia

```bash
faro evidence verify -c mi-campana             # comprueba el SHA-256 de cada captura
faro evidence list telegram:123:456 -c mi-campana
faro evidence reveal te_a1b2… -c mi-campana --yes   # seudónimo → cuenta real, solo uso interno
```

## Principios que impone el código

- **Solo fuentes públicas.** Telethon solo entra en canales y grupos por nombre de usuario; yt-dlp,
  solo en páginas públicas.
- **Seudónimos por defecto.** Todo lo que sale (tablas, informe, GEXF) lleva un seudónimo HMAC con
  clave local. El mapa real está en la tabla `identities`, y solo se consulta pidiéndolo.
- **Evidencia inmutable.** Cada captura se escribe una vez con su `.sha256`, y cada registro apunta a
  su captura.
- **La coordinación exige al menos dos señales.** El informe lo dice en su propio texto.

## Salida por proxy

Toda la captación en clearnet puede salir por un proxy. Se fija en
`~/.config/faro/credentials.env`, por colector o para todos:

```
FARO_PROXY=socks5h://127.0.0.1:1080          # comodín
FARO_PROXY_YTDLP=…                           # YouTube y TikTok: conviene IP residencial
FARO_PROXY_TELEGRAM=…                        # Telegram: conviene IP fija
FARO_PROXY_HTTP=…                            # RDAP, HIBP, GitHub
```

Si el proxy no responde, el colector sale directo y lo avisa. Tor usa su propio `FARO_TOR_PROXY`.
La capa LLM no pasa por el proxy.

## Limitaciones conocidas

- TikTok por hashtag no funciona con yt-dlp; hay que usar cuentas y URLs.
- Facebook y WhatsApp quedan fuera: no tienen acceso público programático.
- La búsqueda de YouTube no ordena por fecha; se filtra después por `posted_at`.

## Tests

```bash
pytest -q
```

## Licencia

MIT. El módulo .onion adapta código de Robin (MIT); ver `NOTICE`.
