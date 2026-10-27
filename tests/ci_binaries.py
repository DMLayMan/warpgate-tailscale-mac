"""Fetch locked official Linux binaries into ignored .ci-bin for CI tests."""
import importlib.util
from pathlib import Path
import tarfile
import shutil

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('wgkit',root/'scripts/wgkit.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
out=root/'.ci-bin';out.mkdir(exist_ok=True)
lock=w.load(root/'config/versions.lock.json')
w.download_asset(lock['warpgate']['amd64'],out/'warpgate')
w.download_asset(lock['caddy']['amd64'],out/'caddy.tgz')
with tarfile.open(out/'caddy.tgz') as a:
    with a.extractfile('caddy') as src,(out/'caddy').open('wb') as dst:shutil.copyfileobj(src,dst)
for name in ('warpgate','caddy'):(out/name).chmod(0o755)
