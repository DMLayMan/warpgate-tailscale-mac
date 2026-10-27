#!/usr/bin/env python3
"""Check the index's actual blob contents before push; not a secret scanner certification."""
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[1]
entries=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=root).split(b'\0')
bad=[]
for entry in entries:
    if not entry:continue
    meta,name=entry.split(b'\t',1);path=name.decode();mode,sha,_=meta.decode().split()
    if mode=='120000':bad.append(path+': symbolic link');continue
    if path.startswith(('local/','runtime/','.ci-bin/')) or path.endswith(('.pem','.key','.env','.sqlite3','.age')):
        bad.append(path+': runtime/secret filename');continue
    data=subprocess.check_output(['git','cat-file','blob',sha],cwd=root)
    if re.search(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{30}',data):
        bad.append(path+': private key material')
    if re.search(rb'WARPGATE_ENCRYPTION_KEY=[A-Za-z0-9+/]{43}=',data):
        bad.append(path+': encryption key literal')
if bad:raise SystemExit('\n'.join(bad))
print('TRACKED_CONTENT_CHECK_PASSED')
