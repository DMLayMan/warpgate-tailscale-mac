"""Download the pinned Mac tmux into ignored test storage; install no services."""
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mac_sessions as sessions

if platform.system() != 'Darwin':
    raise SystemExit('This fixture downloader is for macOS only')
lock = json.loads((ROOT / 'config/versions.lock.json').read_text())['tmux_macos']
arch = {'arm64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
asset = lock[arch]
archive = sessions.fetch(asset)
data = sessions.binary_from_archive(archive, asset['binary_sha256'])
out = ROOT / '.ci-bin/tmux'
out.parent.mkdir(exist_ok=True)
assets = out.parent / 'tmux-assets'
assets.mkdir(exist_ok=True)
(assets / asset['url'].rsplit('/', 1)[1]).write_bytes(archive)
(assets / 'LICENSES.tar.gz').write_bytes(sessions.fetch(lock['licenses']))
out.write_bytes(data); out.chmod(0o700)
print('PINNED_TMUX_TEST_BINARY_READY')
