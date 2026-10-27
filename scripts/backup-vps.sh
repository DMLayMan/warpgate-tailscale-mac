#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ "${1:-}" != --on-vps || "$(uname -s)" != Linux || "$EUID" -ne 0 || $# -ne 3 ]]; then
  echo 'Usage on the VPS: sudo bash scripts/backup-vps.sh --on-vps AGE_PUBLIC_RECIPIENT /absolute/backup.age' >&2
  exit 2
fi
recipient=$2
output=$3
[[ "$recipient" == age1* && "$output" == /* && "$output" == *.age && ! -e "$output" && ! -L "$output" ]]
resolved_output=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$output")
case "$resolved_output" in /opt/warpgate-gateway|/opt/warpgate-gateway/*) echo 'Backup must be outside the runtime directory' >&2; exit 2;; esac
command -v age >/dev/null
command -v systemctl >/dev/null
[[ -d /opt/warpgate-gateway/data ]]
was_active=0
if systemctl is-active --quiet warpgate-gateway; then
  was_active=1
  systemctl stop warpgate-gateway
fi
restart_gateway() {
  if [[ "$was_active" == 1 ]]; then systemctl start warpgate-gateway; fi
}
trap restart_gateway EXIT
# Open the destination without following/overwriting existing files.
set -o noclobber
if ! tar -C /opt/warpgate-gateway -cf - config data public | age -r "$recipient" > "$output"; then
  echo 'Backup failed; incomplete output retained for inspection.' >&2
  exit 1
fi
chmod 600 "$output"
echo 'ENCRYPTED_BACKUP_CREATED. Encryption.env is deliberately excluded; back it up separately in your password manager.'
