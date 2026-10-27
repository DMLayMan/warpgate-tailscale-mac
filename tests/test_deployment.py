import base64
import importlib.util
import json
import os
import platform
from pathlib import Path
import socket
import ssl
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.request
import urllib.error
import http.cookiejar
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,PROJECT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
wg=module('wgkit');mac=module('setup_mac')

class Configuration(unittest.TestCase):
    def site(self,**changes):
        return {'domain':'ssh.example.com','public_ip':'203.0.113.10','acme_email':'','backend_port':18888,**changes}

    def test_input_rejects_config_injection(self):
        for changes in ({'domain':'x.com\nadmin off'},{'domain':'https://x.com'},
                        {'acme_email':'x@y.com\nreverse_proxy x'},{'backend_port':True},
                        {'backend_port':443},{'public_ip':'not-an-ip'}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as d:
                p=Path(d)/'site.json';p.write_text(json.dumps(self.site(**changes)))
                with self.assertRaises(ValueError):wg.site_config(p)

    def test_listener_and_proxy_security(self):
        cfg,caddy=wg.render(self.site(),Path('/opt/warpgate-gateway'))
        self.assertEqual(cfg['http']['listen'],'127.0.0.1:18888')
        for proto in ('ssh','mysql','postgres','rdp','vnc','kubernetes'):self.assertFalse(cfg[proto]['enable'])
        self.assertEqual(cfg['ssh']['host_key_verification'],'auto_reject')
        self.assertIn('tls_trust_pool file',caddy)
        self.assertNotIn('insecure_skip_verify',caddy)
        self.assertIn('X-Forwarded-For {remote_host}',caddy)
        self.assertNotIn('@@',caddy)

    def test_policy_has_enforced_mfa_and_rejects_unknown_hosts(self):
        policy=wg.load(PROJECT/'config/hardening.json')
        self.assertEqual(policy['mfa_enforcement'],'Require')
        self.assertEqual(policy['ssh_host_key_verification'],'AutoReject')
        self.assertTrue(policy['login_protection_enabled'])
        self.assertFalse(policy['ticket_self_service_enabled'])

    def test_vps_install_cannot_run_on_client(self):
        with mock.patch.object(wg.platform,'system',return_value='Darwin'):
            with self.assertRaises(ValueError):wg.require_vps(True)
        with self.assertRaises(ValueError):wg.require_vps(False)

    def test_mac_authorization_is_source_restricted_and_scoped(self):
        text=mac.ssh_settings('remoteoperator')
        self.assertIn('Match User remoteoperator',text)
        self.assertIn('DisableForwarding yes',text)
        self.assertIn('PasswordAuthentication no',text)
        self.assertIn('/etc/ssh/warpgate_authorized_keys/remoteoperator',text)
        self.assertTrue(text.endswith('Match all\n'))
        for value in ('root\nMatch all','name with spaces','-root'):
            with self.assertRaises(ValueError):mac.ssh_settings(value)

    def test_tailnet_addresses_only(self):
        self.assertEqual(mac.tailnet('100.64.1.2'),'100.64.1.2')
        for ip in ('192.168.1.1','100.128.0.1','127.0.0.1','0.0.0.0'):
            with self.assertRaises(ValueError):mac.tailnet(ip)

    def test_public_key_validation_rejects_private_key(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\n')
            with self.assertRaises(ValueError):mac.public_key(p)
            p.write_text('ssh-ed25519 '+base64.b64encode(b'wrong').decode())
            with self.assertRaises(ValueError):mac.public_key(p)

    def test_locked_assets_have_hashes_and_official_sources(self):
        lock=wg.load(PROJECT/'config/versions.lock.json')
        for name in ('warpgate','caddy'):
            for arch in ('amd64','arm64'):
                a=lock[name][arch]
                self.assertRegex(a['sha256'],r'^[a-f0-9]{64}$')
                self.assertTrue(a['url'].startswith('https://github.com/'))

    def test_receipt_generates_target_without_legacy_algorithms(self):
        import argparse
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            host='ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+bytes(32)).decode()
            receipt={'username':'remoteoperator','mac_tailnet_ip':'100.64.1.20','vps_tailnet_ip':'100.64.1.10','host_public_key':host}
            (d/'receipt.json').write_text(json.dumps(receipt));(d/'id').write_text('11111111-1111-4111-8111-111111111111')
            wg.prepare_target(argparse.Namespace(receipt=d/'receipt.json',key_id=d/'id',output=d/'out'))
            target=wg.load(d/'out/target.json')
            self.assertFalse(target['options']['allow_insecure_algos'])
            self.assertTrue(target['ticket_requests_disabled'])
            self.assertEqual(target['options']['auth']['kind'],'PublicKey')

    def test_services_have_no_bootstrap_token_or_root_app(self):
        for name in ('warpgate-gateway.service','warpgate-https.service'):
            data=(PROJECT/'config'/name).read_text()
            self.assertIn('NoNewPrivileges=true',data)
            self.assertIn('ProtectSystem=strict',data)
            self.assertNotIn('--enable-admin-token',data)
            self.assertNotIn('User=root',data)

    @unittest.skipUnless(platform.system()=='Darwin','Native macOS sshd configuration parser check')
    def test_native_macos_sshd_accepts_scoped_hardening(self):
        # Parse a disposable config; do not start sshd or edit system files.
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(d/'host')],check=True)
            include=d/'conf.d';include.mkdir()
            (include/'100-macos.conf').write_text('UsePAM yes\nAcceptEnv LANG LC_*\nSubsystem sftp /usr/libexec/sftp-server\n')
            (include/'999-warpgate-remoteoperator.conf').write_text(mac.ssh_settings('remoteoperator'))
            cfg=d/'sshd_config'
            cfg.write_text(f'Port 18899\nListenAddress 127.0.0.1\nHostKey {d}/host\nInclude {include}/*\nAuthorizedKeysFile .ssh/authorized_keys\n')
            subprocess.run(['/usr/sbin/sshd','-t','-f',str(cfg)],check=True)
            result=subprocess.check_output(['/usr/sbin/sshd','-T','-f',str(cfg),'-C','user=remoteoperator,host=localhost,addr=100.64.1.10'],text=True)
            self.assertIn('disableforwarding yes',result)
            self.assertIn('passwordauthentication no',result)
            self.assertIn('authorizedkeysfile /etc/ssh/warpgate_authorized_keys/remoteoperator',result)
            unrelated=subprocess.check_output(['/usr/sbin/sshd','-T','-f',str(cfg),'-C','user=unrelated,host=localhost,addr=100.64.1.10'],text=True)
            self.assertIn('authorizedkeysfile .ssh/authorized_keys\n',unrelated)
            self.assertIn('disableforwarding no',unrelated)

@unittest.skipUnless(os.getenv('WG_TEST_BINARY'),'Set WG_TEST_BINARY to run real loopback initialization')
class RealBootstrap(unittest.TestCase):
    def test_real_database_mfa_policy_tls_and_private_key_encryption(self):
        with tempfile.TemporaryDirectory(prefix='wgkit-test-') as d:
            root=Path(d)
            for directory in ('data','config','public','secrets'):(root/directory).mkdir()
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            site={'domain':'ssh.example.com','public_ip':'203.0.113.10','acme_email':'','backend_port':port}
            wg.certificate(root)
            key=base64.b64encode(os.urandom(32)).decode()
            try:
                wg.initialize(Path(os.environ['WG_TEST_BINARY']),root,site,'Review-Only-Password-789!',key)
            except Exception:
                # Emit only the ending of fixture logs; no real credentials involved.
                print((root/'secrets/bootstrap.log').read_text()[-6000:]);raise
            with sqlite3.connect(root/'data/db/db.sqlite3') as db:
                rows=db.execute('SELECT secret_key FROM ssh_client_keys').fetchall()
                self.assertTrue(rows)
                self.assertTrue(all('wgenc:v1:' in value for value, in rows))
            self.assertTrue((root/'public/gateway-ed25519.pub').read_text().startswith('ssh-ed25519 '))
            caddy=os.getenv('CADDY_TEST_BINARY')
            if caddy:
                subprocess.run([caddy,'validate','--config',str(root/'public/Caddyfile'),'--adapter','caddyfile'],check=True)
            # Production mode: no admin-token flag. A password-only login must
            # not reach privileged APIs before the enforced TOTP enrollment.
            env={**os.environ,'WARPGATE_ENCRYPTION_KEY':key}
            for n in ('WARPGATE_ADMIN_TOKEN','WARPGATE_ADMIN_PASSWORD'):env.pop(n,None)
            with (root/'secrets/normal-run.log').open('wb') as log:
                proc=subprocess.Popen([os.environ['WG_TEST_BINARY'],'-c',str(root/'config/warpgate.yaml'),'run'],env=env,stdout=log,stderr=log)
                try:
                    import time
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(root/'public/backend.crt'))))
                    url=f'https://127.0.0.1:{port}'
                    for _ in range(100):
                        try:
                            with opener.open(url+'/@warpgate/api/info',timeout=1):pass
                            break
                        except urllib.error.URLError:time.sleep(.1)
                    req=urllib.request.Request(url+'/@warpgate/api/auth/login',data=json.dumps({'username':'admin','password':'Review-Only-Password-789!'}).encode(),headers={'Content-Type':'application/json'})
                    with opener.open(req,timeout=5):pass
                    with self.assertRaises(urllib.error.HTTPError) as denial:
                        opener.open(url+'/@warpgate/admin/api/targets',timeout=5)
                    self.assertIn(denial.exception.code,(401,403))
                finally:
                    proc.terminate();proc.wait(timeout=10)

if __name__=='__main__':unittest.main()
