"""Disposable-home and PTY tests. Never configure real accounts or services."""
import hashlib
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import pty
import select
import fcntl
import struct
import termios
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mac_sessions as sessions
import setup_mac as mac


def archive(data=b'binary', name='tmux', kind=tarfile.REGTYPE):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as bundle:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = len(data) if kind == tarfile.REGTYPE else 0
        bundle.addfile(item, io.BytesIO(data))
    return out.getvalue()


class SessionFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wgt-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.binary = self.home / 'test tmux'

    def test_preserves_existing_rc_and_repeat_is_noop(self):
        original = 'export PERSONAL_SETTING=keep\n'
        rc = self.home / '.zshrc'
        rc.write_text(original)
        with mock.patch.object(sessions.subprocess, 'run'):
            first = sessions.configure(self.home, self.binary)
            self.assertEqual(first['changed_files'], 3)
            before = rc.stat().st_mtime_ns
            second = sessions.configure(self.home, self.binary)
        self.assertTrue(rc.read_text().startswith(original))
        self.assertEqual(rc.read_text().count(sessions.BEGIN), 1)
        self.assertEqual(second, {'changed_files': 0, 'backup': None})
        self.assertEqual(rc.stat().st_mtime_ns, before)
        manifest = json.loads((Path(first['backup']) / 'manifest.json').read_text())
        saved = next(m for m in manifest if m['path'] == str(rc))
        self.assertEqual(Path(saved['backup']).read_text(), original)

    def test_unmanaged_wrapper_aborts_without_changing_rc(self):
        path = self.home / '.local/bin/remote-resume'
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\necho existing\n')
        with self.assertRaisesRegex(ValueError, 'Unmanaged'):
            sessions.configure(self.home, self.binary)
        self.assertFalse((self.home / '.zshrc').exists())
        self.assertIn('existing', path.read_text())

    def test_symlink_component_and_hardlink_refused(self):
        outside = self.home / 'outside'
        outside.mkdir()
        (self.home / '.config').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            sessions.preflight(self.home)
        (self.home / '.config').unlink()
        rc = self.home / '.zshrc'
        rc.write_text('original')
        os.link(rc, self.home / 'alias')
        with self.assertRaisesRegex(ValueError, 'hardlinked'):
            sessions.preflight(self.home)
        self.assertEqual(rc.read_text(), 'original')

    def test_custom_zdotdir_and_ambiguous_block_refused(self):
        (self.home / '.zshenv').write_text('ZDOTDIR=$HOME/custom\n')
        with self.assertRaisesRegex(ValueError, 'ZDOTDIR'):
            sessions.preflight(self.home)
        for value in (sessions.BEGIN, sessions.END, sessions.END + sessions.BEGIN,
                      sessions.HOOK + sessions.HOOK):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sessions.updated_zshrc(value)

    def test_partial_file_failure_restores_applied_files(self):
        rc = self.home / '.zshrc'
        rc.write_text('original\n')
        real_write = sessions.atomic_write
        def fail_config(path, data, mode):
            if path.name == 'tmux.conf':
                raise OSError('injected disk failure')
            return real_write(path, data, mode)
        with mock.patch.object(sessions.subprocess, 'run'), \
             mock.patch.object(sessions, 'atomic_write', side_effect=fail_config), \
             self.assertRaisesRegex(OSError, 'disk failure'):
            sessions.configure(self.home, self.binary)
        self.assertEqual(rc.read_text(), 'original\n')
        self.assertFalse((self.home / '.local/bin/remote-resume').exists())

    def test_archive_rejects_paths_links_duplicates_and_bad_hash(self):
        expected = hashlib.sha256(b'binary').hexdigest()
        self.assertEqual(sessions.binary_from_archive(archive(), expected), b'binary')
        for data in (archive(name='../tmux'), archive(kind=tarfile.SYMTYPE), archive(b'wrong')):
            with self.assertRaises(ValueError):
                sessions.binary_from_archive(data, expected)

    def test_binary_reused_without_download_and_mismatch_refused(self):
        binary = b'binary'
        digest = hashlib.sha256(binary).hexdigest()
        lock = {'version': '3.7c', 'arm64': {'binary_sha256': digest},
                'licenses': {'sha256': hashlib.sha256(b'license').hexdigest()}}
        path = self.home / '.local/share/warpgate-tmux/3.7c/tmux'
        path.parent.mkdir(parents=True)
        path.write_bytes(binary); path.chmod(0o700)
        (path.parent / 'LICENSES.tar.gz').write_bytes(b'license')
        download = mock.Mock(side_effect=AssertionError('should not download'))
        with mock.patch.object(sessions.subprocess, 'check_output', return_value='tmux 3.7c\n'):
            self.assertEqual(sessions.install_binary(self.home, lock, 'arm64', download), path)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            sessions.install_binary(self.home, lock, 'arm64', download)
        download.assert_not_called()

    def test_failed_download_publishes_no_binary(self):
        lock = {'version': '3.7c', 'arm64': {'binary_sha256': '0'*64}, 'licenses': {}}
        with self.assertRaises(ValueError):
            sessions.install_binary(self.home, lock, 'arm64', lambda _: archive())
        self.assertFalse((self.home / '.local/share/warpgate-tmux/3.7c/tmux').exists())

    def test_fetch_rejects_wrong_source_or_digest(self):
        with self.assertRaises(ValueError):
            sessions.fetch({'url': 'http://example.com/tmux'})
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.url = 'https://example.com/asset'
        response.read.return_value = b'invalid'
        with mock.patch.object(sessions.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                sessions.fetch({'url': 'https://github.com/tmux/tmux-builds/releases/download/v3.7c/test', 'sha256': '0'*64})

    def test_restrictive_umask_does_not_hide_public_keys(self):
        previous = os.umask(0o077)
        try:
            path = self.home / 'public-keys'
            mac.prepare_key_directory(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        finally:
            os.umask(previous)

    def test_existing_ssh_binding_is_idempotent_but_conflicts_stop(self):
        path = self.home / 'key'
        self.assertFalse(mac.existing_matches(path, 'expected', 0o644))
        path.write_text('expected'); path.chmod(0o644)
        self.assertTrue(mac.existing_matches(path, 'expected', 0o644))
        with self.assertRaises(ValueError):
            mac.existing_matches(path, 'new binding', 0o644)

    def test_receipt_repeat_no_write_and_update_has_backup(self):
        path = self.home / 'receipt.json'
        mac.write_receipt(path, {'version': 1})
        before = path.stat().st_mtime_ns
        mac.write_receipt(path, {'version': 1})
        self.assertEqual(before, path.stat().st_mtime_ns)
        mac.write_receipt(path, {'version': 2})
        backup = next(self.home.glob('receipt.json.before-*'))
        self.assertEqual(json.loads(backup.read_text()), {'version': 1})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_parent_drops_privileges_and_clears_inherited_environment(self):
        account = types.SimpleNamespace(pw_name='serveruser', pw_uid=502, pw_gid=20,
                                        pw_dir=str(self.home), pw_shell='/bin/zsh')
        with mock.patch.object(mac.platform, 'mac_ver', return_value=('15.0', (), '')), \
             mock.patch.object(mac.subprocess, 'run', return_value=types.SimpleNamespace(stdout='{}')) as run:
            mac.session_setup(account)
        options = run.call_args.kwargs
        self.assertEqual(options['user'], 502)
        self.assertEqual(options['group'], 20)
        self.assertEqual(options['extra_groups'], [])
        self.assertEqual(options['env']['HOME'], str(self.home))
        self.assertNotIn('SSH_AUTH_SOCK', options['env'])
        self.assertNotIn('PYTHONPATH', options['env'])

    def test_old_macos_and_non_zsh_account_stop_before_child(self):
        account = types.SimpleNamespace(pw_shell='/bin/bash')
        with self.assertRaisesRegex(ValueError, 'zsh'):
            mac.session_setup(account)
        account.pw_shell = '/bin/zsh'
        with mock.patch.object(mac.platform, 'mac_ver', return_value=('14.0', (), '')), \
             self.assertRaisesRegex(ValueError, 'macOS 15'):
            mac.session_setup(account)

    def test_server_guard_rejects_client_and_nonroot(self):
        with self.assertRaises(ValueError):
            mac.require_server(False)
        with mock.patch.object(mac.platform, 'system', return_value='Linux'), self.assertRaises(ValueError):
            mac.require_server(True)
        with mock.patch.object(mac.platform, 'system', return_value='Darwin'), \
             mock.patch.object(mac.os, 'geteuid', return_value=501), self.assertRaises(ValueError):
            mac.require_server(True)


class MacEntryPoint(unittest.TestCase):
    """Exercise the root entry's orchestration with a fake SSH filesystem."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wgt-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ssh = self.root/'ssh'
        self.ssh.mkdir()
        (self.ssh/'sshd_config.d').mkdir()
        (self.ssh/'sshd_config').write_text('Include /etc/ssh/sshd_config.d/*\n')
        key = 'ssh-ed25519 ' + base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+bytes(32)).decode()
        (self.ssh/'ssh_host_ed25519_key.pub').write_text(key)
        self.key = self.root/'gateway.pub'
        self.key.write_text(key)
        self.receipt = self.root/'mac-receipt.json'
        self.argv = ['setup_mac.py', '--on-server', '--user', 'serveruser', '--gateway-key', str(self.key),
                     '--vps-tailnet-ip', '100.64.1.10', '--mac-tailnet-ip', '100.64.1.20',
                     '--receipt', str(self.receipt)]
        self.keyfile = self.ssh/'warpgate_authorized_keys/serveruser'
        self.dropin = self.ssh/'sshd_config.d/999-warpgate-serveruser.conf'

    def execute(self, session_failure=False, extra=(), baseline_policy=(), outside_changed=False):
        def remap(value):
            value = str(value)
            return self.ssh/value.removeprefix('/etc/ssh/') if value.startswith('/etc/ssh/') else Path(value)
        def output(command, **kwargs):
            if command[0].endswith('dsmemberutil'): raise AssertionError('Existing administrator accounts must not be rejected')
            if not self.dropin.exists() or 'addr=127.0.0.1' in command[-1]:
                baseline=dict(line.split(' ',1) for line in ['authenticationmethods any','forcecommand none','authorizedkeysfile .ssh/authorized_keys',
                                  'pubkeyauthentication yes','passwordauthentication yes','disableforwarding no'])
                baseline.update(baseline_policy)
                if outside_changed and self.dropin.exists():baseline['disableforwarding']='yes'
                return '\n'.join(k+' '+v for k,v in baseline.items())
            return '\n'.join(['authenticationmethods publickey','forcecommand none','pubkeyauthentication yes',
                'passwordauthentication no', 'kbdinteractiveauthentication no', 'disableforwarding yes',
                'permittty yes', 'permituserrc no', 'authorizedkeysfile /etc/ssh/warpgate_authorized_keys/serveruser .ssh/authorized_keys'])
        account = types.SimpleNamespace(pw_uid=502)
        result = {'enabled': True, 'version': '3.7c', 'socket': 'warpgate-work', 'session': 'work'}
        with mock.patch.object(sys, 'argv', [*self.argv, *extra]), \
             mock.patch.object(mac, 'require_server'), mock.patch.object(mac, 'Path', side_effect=remap), \
             mock.patch.object(mac.pwd, 'getpwnam', return_value=account), \
             mock.patch.object(mac.subprocess, 'check_output', side_effect=output), \
             mock.patch.object(mac.subprocess, 'run'), mock.patch.object(mac.os, 'chown'), \
             mock.patch.object(mac, 'session_setup', return_value=result,
                               side_effect=ValueError('download failed') if session_failure else None) as setup, \
             contextlib.redirect_stdout(io.StringIO()):
            mac.main()
            return setup.call_count

    def test_one_command_installs_sessions_and_repeats_without_rebinding(self):
        self.assertEqual(self.execute(), 1)
        before = [p.read_bytes() for p in (self.keyfile, self.dropin, self.receipt)]
        self.assertEqual(self.execute(), 1)
        self.assertEqual(before, [p.read_bytes() for p in (self.keyfile, self.dropin, self.receipt)])
        self.assertTrue(json.loads(self.receipt.read_text())['terminal_session']['enabled'])

    def test_download_failure_rolls_back_only_new_ssh_files(self):
        with self.assertRaisesRegex(ValueError, 'download failed'):
            self.execute(session_failure=True)
        self.assertFalse(self.keyfile.exists())
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.receipt.exists())
        self.execute()
        previous = self.receipt.read_bytes()
        with self.assertRaises(ValueError): self.execute(session_failure=True)
        self.assertTrue(self.keyfile.exists())
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.receipt.read_bytes(), previous)

    def test_ssh_only_skips_all_session_work(self):
        self.assertEqual(self.execute(extra=['--without-tmux']), 0)
        self.assertNotIn('terminal_session', json.loads(self.receipt.read_text()))

    def test_changed_gateway_binding_is_not_silently_replaced(self):
        self.execute()
        previous = self.keyfile.read_bytes()
        self.key.write_text('ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+bytes([1])*32).decode())
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.execute()
        self.assertEqual(self.keyfile.read_bytes(), previous)

    def test_old_account_wide_policy_requires_explicit_migration(self):
        old='Match User serveruser\n    PasswordAuthentication no\nMatch all\n'
        self.dropin.write_text(old)
        with self.assertRaisesRegex(ValueError,'Old account-wide'):
            self.execute(extra=['--without-tmux'])
        self.assertEqual(self.dropin.read_text(),old)
        self.assertFalse(self.keyfile.exists())

    def test_existing_stronger_policy_is_never_weakened(self):
        for policy in ({'authenticationmethods':'publickey,keyboard-interactive'}, {'forcecommand':'/usr/local/bin/restricted-shell'}):
            with self.subTest(policy=policy), self.assertRaisesRegex(ValueError,'manual integration'):
                self.execute(extra=['--without-tmux'],baseline_policy=policy)
            self.assertFalse(self.keyfile.exists())
            self.assertFalse(self.dropin.exists())
            self.assertFalse(self.receipt.exists())

    def test_other_source_change_rolls_back_new_ssh_files(self):
        with self.assertRaisesRegex(ValueError,'Unrelated source'):
            self.execute(extra=['--without-tmux'],outside_changed=True)
        self.assertFalse(self.keyfile.exists())
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.receipt.exists())


TMUX = os.environ.get('TMUX_TEST_BINARY') or shutil.which('tmux')

@unittest.skipUnless(platform.system() == 'Darwin' and os.getenv('TMUX_TEST_ASSETS'),
                     'Verified macOS release fixtures required for fresh install test')
class PinnedInstall(unittest.TestCase):
    def test_fresh_verified_install_and_repeat_in_disposable_home(self):
        lock = json.loads((ROOT/'config/versions.lock.json').read_text())['tmux_macos']
        def download(asset):
            data = (Path(os.environ['TMUX_TEST_ASSETS'])/asset['url'].rsplit('/', 1)[1]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), asset['sha256'])
            return data
        with tempfile.TemporaryDirectory(prefix='wgt-', dir='/tmp') as name:
            home = Path(name)
            binary = sessions.install_binary(home, lock, download=download)
            self.assertEqual(subprocess.check_output([str(binary), '-V'], text=True).strip(), 'tmux '+lock['version'])
            sessions.configure(home, binary)
            before = binary.stat().st_mtime_ns
            sessions.install_binary(home, lock, download=mock.Mock(side_effect=AssertionError('unexpected download')))
            self.assertEqual(sessions.configure(home, binary)['changed_files'], 0)
            self.assertEqual(binary.stat().st_mtime_ns, before)

@unittest.skipUnless(TMUX and shutil.which('zsh'), 'Real tmux and zsh required for isolated PTY checks')
class RealTerminal(unittest.TestCase):
    def test_task_survives_transport_loss_and_next_login_resumes_same_pane(self):
        with tempfile.TemporaryDirectory(prefix='wgt-', dir='/tmp') as directory:
            home = Path(directory)
            sessions.configure(home, Path(TMUX))
            # Keep both the login shell and tmux's child shell independent of
            # runner-wide completion prompts, while still loading our .zshrc.
            (home/'.zshenv').write_text('unsetopt GLOBAL_RCS\n')
            env = {'HOME': str(home), 'PATH': '/usr/bin:/bin', 'TERM': 'xterm-256color',
                   'SHELL': '/bin/zsh', 'LANG': 'en_US.UTF-8', 'TMUX_TMPDIR': str(home),
                   'SSH_CONNECTION': '100.64.1.10 50000 100.64.1.20 22'}
            def tmux(*args):
                return subprocess.check_output([TMUX, '-L', 'warpgate-work', *args], env=env,
                                               text=True, stderr=subprocess.DEVNULL)
            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    # A real terminal consumes paint output. An unread Linux PTY
                    # can fill and block tmux before it processes keyboard input.
                    for _, fd in clients:
                        if fd is not None and select.select([fd], [], [], 0)[0]:
                            try: os.read(fd, 65536)
                            except OSError: pass
                    try:
                        if predicate(): return
                    except (subprocess.CalledProcessError, FileNotFoundError, ValueError): pass
                    time.sleep(.05)
                pane = tmux('capture-pane', '-p')
                self.fail('Timed out waiting for isolated terminal state: ' + pane[-1500:])
            clients = []
            def connect():
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
                proc = subprocess.Popen(['/bin/zsh', '-d', '-i'], stdin=slave, stdout=slave,
                                        stderr=slave, env=env, start_new_session=True)
                os.close(slave); clients.append((proc, master))
                wait_for(lambda: tmux('display-message', '-p', '#{session_attached}').strip() == '1')
                return proc, master
            try:
                first, master = connect()
                identity = tmux('display-message', '-p', '#{pid}:#{session_id}:#{pane_id}:#{pane_pid}')
                os.write(master, b"i=0; while true; do i=$((i+1)); print $i > \"$HOME/counter\"; sleep 0.1; done\n")
                wait_for(lambda: int((home/'counter').read_text()) >= 2)
                before = int((home/'counter').read_text())
                # Closing the PTY models abrupt transport loss, not tmux detach.
                os.close(master); clients[0] = (first, None)
                wait_for(lambda: tmux('display-message', '-p', '#{session_attached}').strip() == '0')
                wait_for(lambda: int((home/'counter').read_text()) > before + 2)
                second, second_master = connect()
                self.assertEqual(identity, tmux('display-message', '-p', '#{pid}:#{session_id}:#{pane_id}:#{pane_pid}'))
                # A noninteractive shell must not attach, even with SSH variables.
                result = subprocess.check_output(['/bin/zsh', '-c', 'source ~/.zshrc; print NONINTERACTIVE_OK'], env=env, text=True)
                self.assertIn('NONINTERACTIVE_OK', result)
                self.assertEqual(tmux('display-message', '-p', '#{session_attached}').strip(), '1')
                # The default shortcut detaches without destroying the session.
                os.write(second_master, b'\x02d')
                wait_for(lambda: tmux('display-message', '-p', '#{session_attached}').strip() == '0')
                self.assertEqual(identity, tmux('display-message', '-p', '#{pid}:#{session_id}:#{pane_id}:#{pane_pid}'))
            finally:
                subprocess.run([TMUX, '-L', 'warpgate-work', 'kill-server'], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for proc, fd in clients:
                    if fd is not None: os.close(fd)
                    if proc.poll() is None: proc.terminate()
                    proc.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
