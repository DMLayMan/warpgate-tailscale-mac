#!/usr/bin/env python3
"""Optional Linux static install. Preserve any existing Tailscale installation."""
import argparse
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
from wgkit import PROJECT, download_asset, load, require_vps, run, write

UNIT='''[Unit]
Description=Tailscale node agent
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
RuntimeDirectory=tailscale
StateDirectory=tailscale
ExecStart=/usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--on-vps',action='store_true');args=p.parse_args()
    require_vps(args.on_vps)
    if shutil.which('tailscale') or shutil.which('tailscaled'):
        print('Existing Tailscale preserved; verify its service/login/policy manually.');return
    dest=Path('/etc/systemd/system/tailscaled.service')
    if dest.exists() or not Path('/dev/net/tun').exists():raise ValueError('Existing service or missing /dev/net/tun; inspect first')
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if not arch:raise ValueError('Unsupported architecture')
    lock=load(PROJECT/'config/versions.lock.json')['tailscale']
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);download_asset(lock[arch],tmp/'tailscale.tgz')
        with tarfile.open(tmp/'tailscale.tgz') as archive:
            for name in ('tailscale','tailscaled'):
                destbin=Path('/usr/local/bin')/name
                if destbin.exists():raise ValueError(f'Conflicting path: {destbin}')
                with archive.extractfile(f"tailscale_{lock['version']}_{arch}/{name}") as src,destbin.open('xb') as out:
                    shutil.copyfileobj(src,out)
                destbin.chmod(0o755)
    write(dest,UNIT,0o644)
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','tailscaled'])
    print('Tailscale installed. Login and apply tag:warpgate-vps as described in docs/TAILSCALE.md; no auth key is stored by this script.')

if __name__=='__main__':
    try:main()
    except ValueError as exc:raise SystemExit(str(exc))
