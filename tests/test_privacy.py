import base64
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_tracked as privacy


class PrivacyChecks(unittest.TestCase):
    def test_documentation_ranges_allowed(self):
        self.assertEqual(privacy.findings('docs/example.md', b'ssh.example.com 203.0.113.10 100.64.1.20'), [])

    def test_real_looking_identifiers_rejected_without_echo(self):
        cases = [b'8.' + b'8.8.8', b'/Users/' + b'private-user/Documents/',
                 b'C:\\Users\\' + b'private-user\\Documents\\',
                 b'person@' + b'mail.invalid', b'ssh-ed25519 ' + base64.b64encode(bytes(51))]
        for value in cases:
            with self.subTest(value=value):
                result = privacy.findings('docs/example.md', value)
                self.assertTrue(result)
                self.assertNotIn(value.decode(), '\n'.join(result))

    def test_runtime_files_and_external_denylist_rejected(self):
        for name in ('local/site.json', 'server-receipt.json', 'evidence.png', 'runtime/db.sqlite3'):
            self.assertTrue(privacy.findings(name, b''))
        self.assertTrue(privacy.findings('README.md', b'private-marker', [b'private-marker']))

    def test_synthetic_credential_is_rejected(self):
        self.assertTrue(privacy.findings('README.md', b'ghp_' + b'0'*36))


if __name__ == '__main__': unittest.main()
