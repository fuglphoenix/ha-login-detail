#!/bin/bash
# Bygger et HA-venv til probe-matricen og peger $ROOT/venv på det.
#
# $1 = etiket, normalt HA-versionen (fx 2026.11.0)
# $2 = pip-spec             (default homeassistant==$1, kan være sti til et wheel)
#
# Alt havner på disk, ikke under /tmp. /tmp på CT 112 er en 2 GB tmpfs (RAM) og
# ét HA-venv fylder ~1,2 G, så to venvs samtidigt under et versionsskift fylder
# filsystemet og giver ENOSPC midt i en probe. Se BOR-156.
#
# To ting ud over selve venv'et skal også ligge på disk:
#   - interpreteren (UV_PYTHON_INSTALL_DIR). Henter uv den selv, lander den i en
#     sessionsbunden tmp-mappe, og venv'ets bin/python peger så på noget der
#     forsvinder, når sessionen gør.
#   - TMPDIR. uv pakker wheels ud gennem TMPDIR; med /tmp går ~1 G gennem RAM
#     under hver build.
#
# homeassistant alene er ikke nok til at køre proberne: frontend, hassil, ffmpeg
# og supervisor-klienten er separate pakker, og uden dem fejler setup af de
# komponenter proberne afhænger af. Derfor HA_PROBE_EXTRAS. mcp_server's egne
# krav flytter sig mellem kerneudgaver - 2026.11 importerer aiohttp_sse direkte i
# mcp_server/http.py, og uden den fejler setup af probe 4 og 6 på afhængigheder.
#
# Overskriv med env vars når matricen kræver det:
#   HA_PROBE_ROOT   rod for runs og venvs   (default /opt/paperclip-data/tmp/ha-probe)
#   HA_PROBE_PY     python-version          (default 3.14)
#   HA_PROBE_EXTRAS pakker ud over HA-core  (default se nedenfor, tom = spring over)
#   HA_PROBE_LINK   1 = flyt $ROOT/venv     (default 0)
#
# $ROOT/venv flyttes IKKE af sig selv. Kører en matrix allerede, peger den på et
# venv, og at flytte symlinket midt i matricen skifter HA-version under den.
set -e
ROOT="${HA_PROBE_ROOT:-/opt/paperclip-data/tmp/ha-probe}"
PY="${HA_PROBE_PY:-3.14}"
EXTRAS="${HA_PROBE_EXTRAS-home-assistant-frontend home-assistant-intents hassil gazetteer-matcher pymicro-vad pyspeex-noise ha-ffmpeg mutagen aiohasupervisor pyotp mcp aiohttp-sse}"
LABEL="$1"
SPEC="${2:-homeassistant==$1}"
[ -n "$LABEL" ] || { echo "brug: $0 <etiket> [pip-spec]" >&2; exit 2; }

export UV_PYTHON_INSTALL_DIR="$ROOT/python"
export TMPDIR="$ROOT/tmp"
mkdir -p "$UV_PYTHON_INSTALL_DIR" "$TMPDIR"

TARGET="$ROOT/venv-$LABEL"
uv python install "$PY"
uv venv --python "$PY" --python-preference only-managed "$TARGET"
uv pip install --python "$TARGET/bin/python" "$SPEC"
[ -n "$EXTRAS" ] && uv pip install --python "$TARGET/bin/python" $EXTRAS

"$TARGET/bin/python" -c 'from homeassistant.const import __version__; print("homeassistant", __version__)'

if [ "${HA_PROBE_LINK:-0}" = "1" ]; then
  ln -sfn "$TARGET" "$ROOT/venv"
  echo "venv -> $TARGET"
else
  echo "venv: urørt ($(readlink "$ROOT/venv" 2>/dev/null || echo 'ikke sat'))"
  echo "kør matricen med: HA_PROBE_VENV=$TARGET tools/run_probe.sh ..."
fi
du -sh "$ROOT"/venv-* 2>/dev/null
df -h "$ROOT" | tail -1
echo "Gamle venv-* bliver liggende. Ryd dem selv, når matricen er kørt færdig."
