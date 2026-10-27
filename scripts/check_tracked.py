#!/usr/bin/env python3
"""Scan staged blobs (optionally history), without printing matched private data."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_NETS = tuple(map(ipaddress.ip_network, (
    '127.0.0.0/8', '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24',
    '100.64.1.0/24',  # Fictional tailnet fixtures, never real receipts.
)))
FIXTURE_IPS = {'0.0.0.0', '192.168.1.1', '100.128.0.1', '100.64.0.0'}


def findings(path, data, deny=()):
    errors = []
    name = Path(path).name.lower()
    if (path.startswith(('local/', 'runtime/', '.ci-bin/')) or
            name.endswith(('.pem', '.key', '.env', '.sqlite3', '.age', '.png', '.jpg', '.log')) or
            'receipt' in name or name in ('setup.json', 'site.json', 'authorized_keys', 'known_hosts')):
        errors.append('runtime/private filename')
    rules = {
        'private key material': rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{30}',
        'encryption key literal': rb'WARPGATE_ENCRYPTION_KEY=[A-Za-z0-9+/]{43}=',
        'credential token': rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|tskey-[A-Za-z0-9_-]{20,})',
        'literal SSH public key': rb'ssh-(?:ed25519|rsa) [A-Za-z0-9+/]{40,}={0,3}',
        'personal home path': rb'/(?:Users|home)/[A-Za-z0-9_.-]+/',
    }
    for label, pattern in rules.items():
        if re.search(pattern, data): errors.append(label)
    for value in re.findall(rb'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', data):
        try: address = ipaddress.ip_address(value.decode())
        except ValueError: continue
        if str(address) not in FIXTURE_IPS and not any(address in net for net in FIXTURE_NETS):
            errors.append('non-example IP literal'); break
    for email in re.findall(rb'[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,}', data):
        # Legacy synthetic injection fixture in the original history only.
        if path == 'tests/test_deployment.py' and email == b'x@' + b'y.com': continue
        domain = email.decode().split('@', 1)[1]
        if domain not in ('example.com', 'example.net', 'example.org') and not domain.endswith('.noreply.github.com'):
            errors.append('non-example email'); break
    if any(value and value in data for value in deny):
        errors.append('operator-provided private identifier')
    return [f'{path}: {label}' for label in errors]


def scan(root=ROOT, history=False, deny=()):
    def git(*args): return subprocess.check_output(['git', *args], cwd=root)
    errors, seen = [], set()
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry: continue
        meta, name = entry.split(b'\t', 1)
        mode, sha, stage = meta.decode().split()
        path = name.decode()
        if mode == '120000' or stage != '0':
            errors.append(path + ': symlink or unresolved index entry'); continue
        errors.extend(findings(path, git('cat-file', 'blob', sha), deny))
        seen.add(sha)
    if history:
        for entry in git('rev-list', '--objects', '--all').decode().splitlines():
            sha, _, path = entry.partition(' ')
            if sha in seen or not path or git('cat-file', '-t', sha).strip() != b'blob': continue
            seen.add(sha)
            errors.extend(findings(path, git('cat-file', 'blob', sha), deny))
        errors.extend(findings('commit metadata', git('log', '--all', '--format=%an <%ae>%n%cn <%ce>%n%B'), deny))
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    # Optional deployment-specific denylist stays OUTSIDE the repository.
    deny_path = os.getenv('WG_PRIVACY_DENYLIST')
    deny = Path(deny_path).read_bytes().splitlines() if deny_path else ()
    errors = scan(history=args.history, deny=deny)
    if errors: raise SystemExit('\n'.join(errors))
    print('TRACKED_CONTENT_CHECK_PASSED' + (' INCLUDING_HISTORY' if args.history else ''))


if __name__ == '__main__': main()
