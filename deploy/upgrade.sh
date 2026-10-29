#!/usr/bin/env bash
# Run from the extracted v2.1 package: sudo bash /tmp/elaine-v2.1/deploy/upgrade.sh
set -euo pipefail
umask 077
if [[ $EUID -ne 0 ]]; then echo 'Gunakan sudo bash deploy/upgrade.sh'; exit 1; fi
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
installed_dir=/opt/elaine-v2
if [[ "$package_dir" == "$installed_dir" ]]; then
  echo 'Untuk upgrade, ekstrak paket ke /tmp/elaine-v2.1 terlebih dahulu.'; exit 1
fi
if [[ ! -f "$installed_dir/.env" || ! -x "$installed_dir/.venv/bin/python" ]]; then
  echo 'Instalasi lama /opt/elaine-v2 tidak ditemukan.'; exit 1
fi
if [[ ! -f "$package_dir/migrations/versions/0002_voice_and_ack_status.py" ]]; then
  echo 'Paket v2.1 tidak lengkap.'; exit 1
fi
python3 - <<'PY'
import ctypes.util
if not ctypes.util.find_library('opus'):
    raise SystemExit('Install dahulu: sudo apt install -y libopus0')
PY
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
backup_dir="/opt/elaine-backups/$stamp"
install -d -m 700 "$backup_dir"
cd "$installed_dir"
# Backup code including local customizations, but exclude runtime data and credentials.
tar --exclude='./data' --exclude='./.venv' --exclude='./.git' --exclude='./.env' \
    --exclude='./__pycache__' -czf "$backup_dir/source-before.tar.gz" .
install -m 600 .env "$backup_dir/env-before"
.venv/bin/python -m pip freeze > "$backup_dir/requirements-before.txt"
for config_file in ai-settings.key ai-settings.enc; do
  if [[ -f "data/$config_file" ]]; then install -m 600 "data/$config_file" "$backup_dir/$config_file"; fi
done
stopped=0
on_error() {
  echo "Upgrade berhenti. Backup: $backup_dir"
  if [[ "$stopped" == 1 ]]; then
    systemctl stop elaine.service || true
    echo 'Service dihentikan agar kode/schema yang berbeda tidak dipakai bersama.'
    echo 'Periksa error di atas dan panduan UPGRADE.md; jangan menghapus data/.'
  fi
}
trap on_error ERR
systemctl stop elaine.service
stopped=1
# CLI v2.0 can back up its own configured database consistently.
backup_name="data/backup-pre-2.1-$stamp.sqlite3"
sudo -u discordbot .venv/bin/elaine backup --output "$backup_name"
install -m 600 "$backup_name" "$backup_dir/database-before.sqlite3"
# Copy only the versioned application paths; preserve .env, data/, .git and .venv.
for directory in src prompts migrations tests deploy docs; do
  install -d -m 755 "$directory"
  cp -R "$package_dir/$directory/." "$directory/"
  find "$directory" -type d -exec chmod 755 {} +
  find "$directory" -type f -exec chmod 644 {} +
done
for filename in README.md DEPLOY.md TESTING.md UPGRADE.md CHANGELOG.md pyproject.toml requirements.lock alembic.ini .env.example .gitignore; do
  install -m 644 "$package_dir/$filename" "$filename"
done
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
chmod -R a+rX .venv
sudo -u discordbot .venv/bin/elaine migrate
sudo -u discordbot .venv/bin/elaine check
systemctl restart elaine.service
# This reports process state; application readiness must also show 'Elaine siap' in logs.
systemctl is-active --quiet elaine.service
stopped=0
trap - ERR
printf '%s\n' 'Kode dan migration v2.1 terpasang.' "Backup: $backup_dir" \
  'Pastikan log baru menunjukkan Elaine siap:' 'sudo journalctl -u elaine.service -n 60 --no-pager' \
  'Lalu uji /admin status, /ingat, /uang, dan /suara tes di Discord.'
