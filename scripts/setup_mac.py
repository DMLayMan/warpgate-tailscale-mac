#!/usr/bin/env python3
"""Run ONLY on the remote Mac; reuse an explicitly selected existing account."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shlex
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]

def require_server(on_server):
    if not on_server or platform.system() != 'Darwin' or os.geteuid() != 0:
        raise ValueError('Requires remote Mac, root and explicit --on-server; never run on the client')

def session_setup(account):
    if account.pw_shell != '/bin/zsh':
        raise ValueError('Automatic sessions require /bin/zsh; use --without-tmux or review the account shell')
    lock = json.loads((PROJECT/'config/versions.lock.json').read_text())['tmux_macos']
    if tuple(map(int, platform.mac_ver()[0].split('.')[:2])) < tuple(map(int, lock['minimum_macos'].split('.'))):
        raise ValueError('Pinned tmux requires macOS 15 or later; use --without-tmux on older systems')
    # Never run user shell files or a package manager as root. The helper source
    # is passed directly because the target user may not traverse the checkout.
    result = subprocess.run(
        [sys.executable, '-c', (PROJECT/'scripts/mac_sessions.py').read_text()],
        input=json.dumps({'on_server': True, 'user': account.pw_name, 'lock': lock}),
        text=True, capture_output=True, check=True, timeout=300,
        user=account.pw_uid, group=account.pw_gid, extra_groups=[], cwd='/',
        env={'HOME': account.pw_dir, 'USER': account.pw_name, 'LOGNAME': account.pw_name,
             'SHELL': '/bin/zsh', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'})
    return json.loads(result.stdout)

def existing_matches(path, content, mode):
    if path.is_symlink():
        raise ValueError(f'Refusing symlink: {path}')
    if not path.exists():
        return False
    st = path.stat()
    if (not path.is_file() or st.st_nlink != 1 or st.st_uid != os.geteuid() or
            (st.st_mode & 0o777) != mode or path.read_text() != content):
        raise ValueError(f'Conflicting managed file: {path}; review migration manually')
    return True

def prepare_key_directory(path):
    if path.is_symlink():
        raise ValueError('Refusing authorized-key directory symlink')
    path.mkdir(mode=0o755, exist_ok=True)
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
        raise ValueError('Authorized-key directory must be installer-owned and not writable by other users')
    # mkdir(mode=755) alone is masked by an inherited umask, potentially making
    # the public key unreadable to sshd after it switches to the target account.
    path.chmod(0o755)

def write_receipt(path, data):
    if path.exists() and json.loads(path.read_text()) == data:
        return
    if path.exists():
        fd, _ = tempfile.mkstemp(prefix=path.name+'.before-', dir=path.parent)
        with os.fdopen(fd, 'wb') as out:
            out.write(path.read_bytes())
    fd, temporary = tempfile.mkstemp(prefix='.mac-receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, indent=2); out.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def tailnet(value):
    ip=ipaddress.ip_address(value)
    if ip not in ipaddress.ip_network('100.64.0.0/10'): raise ValueError('Expected a Tailscale IPv4 address')
    return str(ip)

def public_key(path):
    parts=Path(path).read_text().strip().split()
    if len(parts)<2 or parts[0]!='ssh-ed25519': raise ValueError('An Ed25519 gateway PUBLIC key is required')
    blob=base64.b64decode(parts[1],validate=True)
    if len(blob)!=51 or blob[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20': raise ValueError('Malformed Ed25519 public key')
    return ' '.join(parts[:2])

def validate_username(username):
    if not re.fullmatch('[A-Za-z_][A-Za-z0-9_.-]{0,63}',username): raise ValueError('Invalid Mac short username')

def effective_settings(username, source):
    output=subprocess.check_output(['/usr/sbin/sshd','-T','-C',f'user={username},host=localhost,addr={source}'],text=True)
    return dict(line.split(' ',1) for line in output.splitlines() if ' ' in line)

def ssh_settings(username, vps, legacy_keys):
    validate_username(username);vps=tailnet(vps)
    paths=[f'/etc/ssh/warpgate_authorized_keys/{username}']
    for path in legacy_keys:
        if not isinstance(path,str) or any(c in path for c in '\r\n"\\'):
            raise ValueError('Unsupported existing AuthorizedKeysFile path; review manually')
        if path not in paths and path!='none':paths.append(path)
    quoted=' '.join('"'+p+'"' for p in paths)
    return f'''# Managed by warpgate-tailscale-mac; existing account, VPS source only (v2).
Match User {username} Address {vps}
    AuthorizedKeysFile {quoted}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    DisableForwarding yes
    PermitTTY yes
    PermitUserRC no
Match all
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--on-server',action='store_true')
    p.add_argument('--user',required=True)
    p.add_argument('--gateway-key')
    p.add_argument('--vps-tailnet-ip')
    p.add_argument('--mac-tailnet-ip')
    p.add_argument('--receipt')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--without-tmux', action='store_true', help='Configure SSH only; do not change existing sessions')
    mode.add_argument('--sessions-only', action='store_true', help='Add persistent sessions to an already prepared server account')
    args=p.parse_args()
    require_server(args.on_server)
    validate_username(args.user)
    account=pwd.getpwnam(args.user)
    if account.pw_uid<500: raise ValueError('Choose an existing regular Mac account, not root or a system account')
    if args.sessions_only:
        print('MAC_SESSIONS_CONFIGURED '+json.dumps(session_setup(account)))
        return
    if not all((args.gateway_key, args.vps_tailnet_ip, args.mac_tailnet_ip, args.receipt)):
        p.error('--gateway-key, --vps-tailnet-ip, --mac-tailnet-ip and --receipt are required for SSH setup')
    vps=tailnet(args.vps_tailnet_ip);mac=tailnet(args.mac_tailnet_ip)
    key=public_key(args.gateway_key)
    main_config=Path('/etc/ssh/sshd_config').read_text()
    if not re.search(r'^\s*Include\s+/etc/ssh/sshd_config\.d/\*',main_config,re.M|re.I):
        raise ValueError('sshd_config does not include /etc/ssh/sshd_config.d/*; add/review Include manually first')
    keydir=Path('/etc/ssh/warpgate_authorized_keys')
    dest=keydir/args.user
    # Match blocks must follow macOS's global UsePAM/Subsystem directives.
    dropin=Path(f'/etc/ssh/sshd_config.d/999-warpgate-{args.user}.conf')
    if dropin.exists() and 'existing account, VPS source only (v2)' not in dropin.read_text():
        raise ValueError('Old account-wide SSH policy detected; follow docs/MAC.md migration before reusing this account')
    baseline=effective_settings(args.user,vps)
    if baseline.get('authenticationmethods') not in ('any','publickey') or baseline.get('forcecommand') not in (None,'none'):
        raise ValueError('Existing multi-factor/forced-command SSH policy requires manual integration; it was not weakened')
    legacy=shlex.split(baseline['authorizedkeysfile'])
    config=ssh_settings(args.user,vps,[p for p in legacy if p!=str(dest)])
    outside=effective_settings(args.user,'127.0.0.1')
    keyline=f'from="{vps}",restrict,pty {key} warpgate-gateway\n'
    key_exists=existing_matches(dest,keyline,0o644)
    dropin_exists=existing_matches(dropin,config,0o600)
    if keydir.is_symlink() or dropin.parent.is_symlink():
        raise ValueError('Refusing managed directory symlink')
    receipt=Path(args.receipt).absolute()
    if receipt.is_symlink() or not receipt.parent.is_dir():
        raise ValueError('Receipt needs an existing private parent directory and must not be a symlink')
    host=Path('/etc/ssh/ssh_host_ed25519_key.pub')
    if not host.exists():
        raise ValueError('Mac host key missing. Initialize Remote Login locally, then rerun')
    data={'schema_version':1,'username':args.user,'vps_tailnet_ip':vps,
          'mac_tailnet_ip':mac,'host_public_key':public_key(host)}
    if receipt.exists():
        previous=json.loads(receipt.read_text())
        if not isinstance(previous,dict) or any(previous.get(k)!=v for k,v in data.items()):
            raise ValueError('Existing receipt belongs to a different binding; choose a new private path')
    prepare_key_directory(keydir)
    dropin.parent.mkdir(mode=0o755,exist_ok=True)
    ok=False;created_key=False;created_dropin=False
    try:
        if not key_exists:
            with dest.open('x') as f:
                created_key=True
                os.fchmod(f.fileno(),0o644);f.write(keyline)
            os.chown(dest,0,0)
        if not dropin_exists:
            with dropin.open('x') as f:
                created_dropin=True
                os.fchmod(f.fileno(),0o600);f.write(config)
        subprocess.run(['/usr/sbin/sshd','-t'],check=True)
        settings=effective_settings(args.user,vps)
        expected={'authenticationmethods':'publickey','pubkeyauthentication':'yes',
                  'passwordauthentication':'no','kbdinteractiveauthentication':'no',
                  'disableforwarding':'yes','permittty':'yes','permituserrc':'no'}
        for name,value in expected.items():
            if settings.get(name)!=value: raise ValueError(f'Existing SSH config overrides {name}; installation rolled back')
        expected_paths=shlex.split(config.split('AuthorizedKeysFile ',1)[1].splitlines()[0])
        if shlex.split(settings['authorizedkeysfile'])!=expected_paths:
            raise ValueError('Existing SSH policy overrides gateway/legacy key paths; installation rolled back')
        if effective_settings(args.user,'127.0.0.1')!=outside:
            raise ValueError('Unrelated source SSH settings changed; installation rolled back')
        if not args.without_tmux:
            sessions=session_setup(account)
            data['terminal_session']={k:sessions[k] for k in ('enabled','version','socket','session')}
            print('MAC_SESSIONS_CONFIGURED '+json.dumps(sessions))
        elif receipt.exists() and 'terminal_session' in previous:
            data['terminal_session']=previous['terminal_session']
        write_receipt(receipt,data)
        ok=True
    finally:
        if not ok:
            if created_key:dest.unlink(missing_ok=True)
            if created_dropin:dropin.unlink(missing_ok=True)
    print(f'MAC_SSH_CONFIGURED. Receipt: {receipt}')
    print('Remote Login, Tailscale policy, actual connectivity and wake/reboot acceptance are separate steps; see docs/MAC.md.')

if __name__=='__main__':
    try: main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.stderr or str(exc))
    except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as exc: raise SystemExit(str(exc))
