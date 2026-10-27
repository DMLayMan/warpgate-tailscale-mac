#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != --on-vps || "$(uname -s)" != Linux || "$EUID" -ne 0 || $# -ne 2 ]]; then
  echo 'Usage: sudo bash scripts/check-vps.sh --on-vps ssh.example.com' >&2
  exit 2
fi
domain=$2
[[ "$domain" =~ ^[a-z0-9.-]+$ ]]
systemctl is-active warpgate-gateway
systemctl is-active warpgate-https
tailscale status
ss -lntp
curl --fail --silent --show-error --max-time 15 "https://$domain/@warpgate/api/info" >/dev/null
openssl x509 -checkend 2592000 -noout -in /opt/warpgate-gateway/data/backend.crt
echo 'BASIC_GATEWAY_CHECK_PASSED. Verify MFA, Mac connectivity, revocation, recordings and restore separately.'
