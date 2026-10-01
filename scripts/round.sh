#!/usr/bin/env bash
# Ronda de captación para cron. Uso: scripts/round.sh <campaña>
# Ejemplo crontab (cada 6 h):  0 */6 * * *  /ruta/a/faro/scripts/round.sh mi-campana
set -euo pipefail
cd "$(dirname "$0")/.."
CAMP="${1:?campaña}"
LOG="$HOME/.local/share/faro/$CAMP/rounds.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== $(date -u +%FT%TZ) ronda $CAMP"
  .venv/bin/faro collect all -c "$CAMP" || echo "ronda con errores"
  .venv/bin/faro evidence verify -c "$CAMP" || echo "!! EVIDENCIA ALTERADA"
} >> "$LOG" 2>&1
