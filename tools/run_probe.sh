#!/bin/bash
# $1 = run letter, $2 = probe domain, $3 = report file, $4 = "ban"|"noban"
#
# Arbejdsområdet ligger på disk, ikke under /tmp. /tmp på CT 112 er en 2 GB
# tmpfs (RAM); ét HA-venv fylder ~1,2 G, så to venvs samtidigt under et
# versionsskift fylder filsystemet og giver ENOSPC midt i en probe. Se BOR-156.
#
# Overskriv med env vars når matricen kræver det:
#   HA_PROBE_ROOT   rod for runs og venvs   (default /opt/paperclip-data/tmp/ha-probe)
#   HA_PROBE_VENV   venv der skal køres     (default $HA_PROBE_ROOT/venv)
#   HA_PROBE_PORT   HTTP-port               (default 18123)
#   HA_PROBE_REPO   repo-rod                (default scriptets eget repo)
set -e
REPO="${HA_PROBE_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT="${HA_PROBE_ROOT:-/opt/paperclip-data/tmp/ha-probe}"
VENV="${HA_PROBE_VENV:-$ROOT/venv}"
PORT="${HA_PROBE_PORT:-18123}"
# Proberne taler HTTP med deres egen instans og læser porten her. Uden exporten
# rammer de default-porten og måler en fremmed instans, hvis der kører en.
export HA_PROBE_PORT="$PORT"
DIR=$ROOT/run$1
mkdir -p "$ROOT"
# Roden deles mellem samtidige kørsler. Uden det her sletter to prober med samme
# run-bogstav hinandens arbejdsmappe under benene på hinanden.
if pgrep -f -- "-m homeassistant -c $DIR( |\$)" >/dev/null 2>&1; then
  echo "$1: run$1 er optaget af en kørende probe — vælg et andet bogstav" >&2
  exit 1
fi
rm -rf "$DIR"; mkdir -p "$DIR/custom_components" "$DIR/.storage"
cp -a "$REPO/custom_components/login_detail" "$DIR/custom_components/"
cp -a "$REPO/tests/custom_components/$2" "$DIR/custom_components/"
python3 - "$REPO" "$DIR" "$2" "$4" "$PORT" <<'PY'
import pathlib, sys
repo, out, domain, ban, port = sys.argv[1:6]
cfg = pathlib.Path(repo, "tests/configuration.yaml").read_text()
cfg = cfg.replace("login_detail:\nlogin_detail_probe:\n", "")
cfg = cfg.replace("    custom_components.login_detail_probe: info\n",
                  f"    custom_components.{domain}: info\n")
if domain == "login_detail_probe":
    cfg += "login_detail:\nlogin_detail_probe:\n"
else:
    cfg += f"{domain}:\n"
cfg += f"""
http:
  server_port: {port}
  server_host: 127.0.0.1
  ip_ban_enabled: {'true' if ban == 'ban' else 'false'}
  login_attempts_threshold: 1000
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
"""
pathlib.Path(out, "configuration.yaml").write_text(cfg)
PY
cd "$DIR" && env -u SUPERVISOR -u SUPERVISOR_TOKEN timeout 300 \
  "$VENV/bin/python" -m homeassistant -c "$DIR" --skip-pip --log-no-color \
  > "$DIR/stdout.log" 2>&1 || true
python3 -c "
import json,sys
try:
    d=json.load(open('$DIR/$3'))
except Exception as e:
    print('$1: NO REPORT', e); sys.exit()
print('$1: %s/%s' % (d['passed'], d['total']))
for c in d['checks']:
    if not c['ok']: print('   FAIL', c['name'], '->', str(c['detail'])[:400])
"
