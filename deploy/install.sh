#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $EUID -ne 0 ]]; then echo 'Jalankan dengan sudo bash deploy/install.sh'; exit 1; fi
root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ "$root_dir" != /opt/elaine-v2 ]]; then echo 'Ekstrak proyek ke /opt/elaine-v2 dahulu.'; exit 1; fi
cd "$root_dir"
python3 - <<'PY'
import ctypes.util
if not ctypes.util.find_library("opus"):
    raise SystemExit("Install dahulu: sudo apt install -y libopus0")
PY
if ! id discordbot >/dev/null 2>&1; then useradd --system --home-dir /opt/elaine-v2 --shell /usr/sbin/nologin discordbot; fi
install -d -o discordbot -g discordbot -m 700 data
if [[ ! -f .env ]]; then
  install -o root -g discordbot -m 640 .env.example .env
  if [[ -f /opt/discord-ai-bot/.env ]]; then
    python3 - <<'PY'
from pathlib import Path
# Copy settings verbatim, then append new database defaults. dotenv uses last value.
p = Path('.env')
p.write_text(Path('/opt/discord-ai-bot/.env').read_text() + '\nDB_PATH=data/elaine_v2.sqlite3\nAI_MODE=json\nDEFAULT_TIMEZONE=Asia/Jakarta\n')
PY
  fi
fi
chown root:discordbot .env
chmod 640 .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps --no-build-isolation -e .
chmod -R a+rX .venv
# Source is readable, secrets and database have separate permissions.
chmod 755 /opt/elaine-v2
find src prompts migrations -type d -exec chmod 755 {} +
find src prompts migrations -type f -exec chmod 644 {} +
chmod 644 alembic.ini pyproject.toml
sudo -u discordbot .venv/bin/elaine migrate
install -o root -g root -m 644 deploy/elaine.service /etc/systemd/system/elaine.service
systemctl daemon-reload
printf '%s\n' 'Instalasi selesai; service BELUM dijalankan.' 'Periksa .env, jalankan check dan probe-ai, lalu ikuti DEPLOY.md untuk cutover bot lama.'
