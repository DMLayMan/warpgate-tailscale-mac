"""Unprivileged session installer, invoked by setup_mac after dropping privileges.

All writes are confined to the explicitly selected account's home. Tests call
the pure/file helpers with disposable homes; they never provision an account.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

MARKER = '# Managed by warpgate session setup v1'
BEGIN = '# BEGIN warpgate tmux v1'
END = '# END warpgate tmux v1'
CONFIG = MARKER + '''
set -s exit-unattached off
set -g destroy-unattached off
set -g default-shell /bin/zsh
set -g history-limit 20000
set -g status-right "detach: Ctrl-b d | %H:%M"
'''
HOOK = '''# BEGIN warpgate tmux v1
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
if [[ -o interactive && -n "${SSH_CONNECTION:-}" && -z "${TMUX:-}" &&
      "${TERM:-dumb}" != dumb && -t 0 && -t 1 &&
      "${REMOTE_SESSION_NO_AUTO:-0}" != 1 &&
      ! -e "$HOME/.config/warpgate-session/no-auto-attach" &&
      -x "$HOME/.local/bin/remote-resume" ]]; then
  if "$HOME/.local/bin/remote-resume"; then
    exit
  else
    print -u2 'Session resume failed; continuing in a normal SSH shell.'
  fi
fi
# END warpgate tmux v1
'''


def safe_path(home, path):
    """Reject links, hardlinked files and foreign-owned components before writes."""
    home, path = Path(home), Path(path)
    path.relative_to(home)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'Refusing symlink: {part}')
        if part.exists():
            st = part.stat()
            if st.st_uid != os.geteuid():
                raise ValueError(f'Path belongs to another account: {part}')
            if part.is_file() and st.st_nlink != 1:
                raise ValueError(f'Refusing hardlinked file: {part}')
        if part == home:
            break
    return path


def directory(home, path):
    safe_path(home, path).mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_write(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix='.wg-session-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            os.fchmod(out.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fetch(asset):
    if not asset['url'].startswith('https://github.com/tmux/tmux-builds/releases/download/'):
        raise ValueError('tmux download must use the locked official source')
    with urllib.request.urlopen(asset['url'], timeout=90) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Download redirected away from HTTPS')
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != asset['sha256']:
        raise ValueError('tmux archive SHA256/size check failed')
    return data


def binary_from_archive(data, expected):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) != 1 or members[0].name != 'tmux' or not members[0].isfile():
            raise ValueError('Unexpected tmux archive structure')
        if members[0].size > 10 * 1024 * 1024:
            raise ValueError('Oversized tmux binary')
        result = archive.extractfile(members[0]).read()
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError('tmux binary SHA256 check failed')
    return result


def install_binary(home, lock, machine=None, download=fetch):
    arch = {'arm64': 'arm64', 'x86_64': 'amd64'}.get(machine or platform.machine())
    if arch is None:
        raise ValueError('Unsupported Mac architecture')
    version = lock['version']
    if not version.replace('.', '').isalnum():
        raise ValueError('Invalid locked tmux version')
    asset = lock[arch]
    dest = home / '.local/share/warpgate-tmux' / version / 'tmux'
    safe_path(home, dest)
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != asset['binary_sha256']:
        raise ValueError('Existing managed tmux differs from lock; review before replacing')
    license_path = dest.parent / 'LICENSES.tar.gz'
    safe_path(home, license_path)
    if license_path.exists() and hashlib.sha256(license_path.read_bytes()).hexdigest() != lock['licenses']['sha256']:
        raise ValueError('Existing tmux license archive differs from lock')
    # Validate every download before publishing an executable. No extractall.
    binary = None if dest.exists() else binary_from_archive(download(asset), asset['binary_sha256'])
    licenses = None if license_path.exists() else download(lock['licenses'])
    directory(home, dest.parent)
    if licenses is not None:
        atomic_write(license_path, licenses, 0o600)
    if binary is not None:
        atomic_write(dest, binary, 0o700)
    if not os.access(dest, os.X_OK):
        raise ValueError('Existing tmux is not executable; inspect permissions')
    actual = subprocess.check_output([str(dest), '-V'], text=True, timeout=10).strip()
    if actual != f'tmux {version}':
        raise ValueError('Installed tmux version does not match the lock')
    return dest


def wrapper(binary):
    return f'''#!/bin/sh
{MARKER}
set -eu
TMUX_BIN={shlex.quote(str(binary))}
CONFIG="$HOME/.config/warpgate-session/tmux.conf"
PATH="$HOME/.local/bin:$PATH"
export PATH
case "${{1:-resume}}" in
  status) exec "$TMUX_BIN" -L warpgate-work list-sessions ;;
  resume) ;;
  *) printf 'Usage: remote-resume [resume|status]\\n' >&2; exit 2 ;;
esac
if [ -n "${{TMUX:-}}" ]; then
  printf 'Already inside tmux; detach with Ctrl-b then d.\\n'
  exit 0
fi
if [ ! -t 0 ] || [ ! -t 1 ]; then
  printf 'An interactive terminal is required.\\n' >&2
  exit 2
fi
exec "$TMUX_BIN" -L warpgate-work -f "$CONFIG" new-session -A -s work
'''


def updated_zshrc(original):
    if BEGIN not in original and END not in original:
        return original + ('\n' if original and not original.endswith('\n') else '') + '\n' + HOOK
    if original.count(BEGIN) != 1 or original.count(END) != 1:
        raise ValueError('Ambiguous managed shell block; review manually')
    start, end = original.index(BEGIN), original.index(END)
    if end < start:
        raise ValueError('Invalid managed shell block order')
    return original[:start] + HOOK.rstrip('\n') + original[end + len(END):]


def preflight(home):
    for name in ('.zshrc', '.zshenv', '.zprofile', '.zlogin',
                 '.local/bin/remote-resume', '.config/warpgate-session/tmux.conf',
                 '.local/state/warpgate-session', '.local/share/warpgate-tmux'):
        safe_path(home, home / name)
    # Do not source an existing startup file to discover configuration.
    for name in ('.zshenv', '.zprofile', '.zshrc', '.zlogin'):
        path = home / name
        if path.exists() and 'ZDOTDIR' in path.read_text():
            raise ValueError('Custom ZDOTDIR detected; use --without-tmux and review shell startup first')
    for name in ('.local/bin/remote-resume', '.config/warpgate-session/tmux.conf'):
        path = home / name
        if path.exists() and MARKER not in path.read_text().splitlines()[:2]:
            raise ValueError(f'Unmanaged session file exists: {path}; migration must be explicit')
    rc = home / '.zshrc'
    updated_zshrc(rc.read_text() if rc.exists() else '')


def configure(home, binary):
    preflight(home)
    rc = home / '.zshrc'
    original = rc.read_text() if rc.exists() else ''
    changes = [(home / '.local/bin/remote-resume', wrapper(binary), 0o700),
               (home / '.config/warpgate-session/tmux.conf', CONFIG, 0o600),
               (rc, updated_zshrc(original), (rc.stat().st_mode & 0o777) if rc.exists() else 0o600)]
    subprocess.run(['/bin/zsh', '-n'], input=changes[-1][1], text=True, check=True)
    subprocess.run(['/bin/sh', '-n'], input=changes[0][1], text=True, check=True)
    pending = [(p, text.encode(), mode) for p, text, mode in changes
               if not p.exists() or p.read_text() != text]
    if not pending:
        return {'changed_files': 0, 'backup': None}
    state = home / '.local/state/warpgate-session'
    directory(home, state)
    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=state))
    manifest, originals, applied = [], {}, []
    for index, (path, data, mode) in enumerate(pending):
        originals[path] = path.read_bytes() if path.exists() else None
        saved = backup / str(index)
        if originals[path] is not None:
            atomic_write(saved, originals[path], 0o600)
        manifest.append({'path': str(path), 'backup': str(saved) if path.exists() else None, 'mode': mode})
    atomic_write(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    try:
        for path, data, mode in pending:
            directory(home, path.parent)
            safe_path(home, path)
            atomic_write(path, data, mode)
            applied.append((path, mode))
    except Exception:
        for path, mode in reversed(applied):
            if originals[path] is None:
                path.unlink()
            else:
                atomic_write(path, originals[path], mode)
        raise
    return {'changed_files': len(pending), 'backup': str(backup)}


def child_main():
    spec = json.load(sys.stdin)
    account = pwd.getpwuid(os.geteuid())
    if (platform.system() != 'Darwin' or os.geteuid() == 0 or
            not spec.get('on_server') or account.pw_name != spec['user']):
        raise ValueError('Session helper requires the explicitly selected, unprivileged Mac server account')
    home = Path(account.pw_dir)
    preflight(home)
    binary = install_binary(home, spec['lock'])
    result = configure(home, binary)
    print(json.dumps({'enabled': True, 'version': spec['lock']['version'],
                      'socket': 'warpgate-work', 'session': 'work', **result}))


if __name__ == '__main__':
    child_main()
