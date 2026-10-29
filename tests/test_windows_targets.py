"""Cross-platform receipt contract; no server state is changed."""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('target_tool', ROOT / 'scripts/wgkit.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class WindowsReceipt(unittest.TestCase):
    def render(self, directory, **changes):
        key = base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes(32)).decode()
        receipt = dict(schema_version=1, backend_os='windows', username='remoteoperator',
                       vps_tailnet_ip='100.64.1.10', server_tailnet_ip='100.64.1.30',
                       port=22, host_public_key='ssh-ed25519 ' + key)
        receipt.update(changes)
        # Each case writes only to a disposable directory.
        (directory / 'receipt.json').write_text(json.dumps(receipt))
        (directory / 'key-id').write_text('11111111-1111-4111-8111-111111111111')
        tool.prepare_target(argparse.Namespace(receipt=directory / 'receipt.json',
                            key_id=directory / 'key-id', output=directory / 'output', name='remote-windows-02'))
        return json.loads((directory / 'output/target.json').read_text())

    def test_windows_target_has_separate_name_and_pinned_identity(self):
        with tempfile.TemporaryDirectory() as d:
            target = self.render(Path(d))
            self.assertEqual(target['name'], 'remote-windows-02')
            self.assertEqual(target['options']['host'], '100.64.1.30')
            self.assertEqual(target['options']['username'], 'remoteoperator')
            self.assertFalse(target['options']['allow_insecure_algos'])

    def test_conflicting_legacy_address_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                self.render(Path(d), mac_tailnet_ip='100.64.1.20')

    def test_existing_administrator_and_space_names(self):
        for user in ('Administrator','Existing.Admin','Daily User','\u7528\u6237'):
            with self.subTest(user=user),tempfile.TemporaryDirectory() as d:
                self.assertEqual(self.render(Path(d),username=user)['options']['username'],user)

    def test_unsafe_and_unsupported_account_names(self):
        for user in ('domain\\user','bad\nMatch all','user*','a"b','trailing ',False):
            with self.subTest(user=user),tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):
                self.render(Path(d),username=user)


if __name__ == '__main__':
    unittest.main()
