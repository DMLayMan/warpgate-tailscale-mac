#!/usr/bin/env python3
"""Small, standard-library deployment helper. No credentials in argv or Git."""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path('/opt/warpgate-gateway')

def load(path):
    return json.loads(Path(path).read_text())

def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def matches(actual, expected):
    # API responses may add optional/null protocol fields to policy objects.
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(k in actual and matches(actual[k],v) for k,v in expected.items())
    return actual==expected

def write(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f'Refusing symlink: {path}')
    path.write_text(content)
    path.chmod(mode)

def site_config(path):
    s = load(path)
    domain = s['domain']
    if not isinstance(domain, str) or len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        raise ValueError('domain must be a lowercase DNS hostname, without scheme/port')
    ip = ipaddress.ip_address(s['public_ip'])
    if ip.version != 4:
        raise ValueError('This deployment expects a public IPv4 A record')
    port = s.get('backend_port', 8888)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('backend_port must be 1024..65535')
    email = s.get('acme_email', '')
    if not isinstance(email, str) or (email and not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email)):
        raise ValueError('Invalid optional ACME email')
    return {**s, 'backend_port':port, 'acme_email':email}

def render(s, root):
    root = Path(root).resolve()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(root)):
        raise ValueError('Runtime root must not contain whitespace or Caddy syntax')
    cfg = {
        'database_url': f'sqlite:{root}/data/db',
        'external_host': s['domain'],
        'http': {'listen':f"127.0.0.1:{s['backend_port']}", 'external_host':s['domain'],
                 'external_port':443, 'certificate':str(root/'data/backend.crt'),
                 'key':str(root/'data/backend.key'), 'trust_x_forwarded_headers':True,
                 'session_max_age':'8h','cookie_max_age':'8h'},
        'ssh': {'enable':False,'host_key_verification':'auto_reject'},
        'mysql':{'enable':False}, 'postgres':{'enable':False},
        'kubernetes':{'enable':False}, 'rdp':{'enable':False}, 'vnc':{'enable':False},
        'log':{'retention':'3days','audit_retention':'90days','format':'json'}
    }
    caddy=(PROJECT/'config/Caddyfile.template').read_text()
    for key,value in {'DOMAIN':s['domain'],'PORT':str(s['backend_port']),
                      'ROOT':str(root),'EMAIL':('tls '+s['acme_email']) if s['acme_email'] else ''}.items():
        caddy=caddy.replace('@@'+key+'@@',value)
    return cfg,caddy

def api(port, cert, token, method, path, body=None):
    req=urllib.request.Request(f'https://127.0.0.1:{port}/@warpgate/admin/api{path}',
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={'X-Warpgate-Token':token,'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
    with opener.open(req, timeout=10) as response:
        data=response.read()
        return json.loads(data) if data else None

def certificate(root):
    # A private internal CA signs a proper end-entity TLS certificate. Only its
    # PUBLIC root goes to Caddy; certificate verification remains enabled.
    quiet={'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
    for path in (root/'secrets/backend-ca.key',root/'data/backend.key'):
        run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256',
             '-pkeyopt','ec_param_enc:named_curve','-out',path],**quiet)
        path.chmod(0o600)
    run(['openssl','req','-x509','-new','-key',root/'secrets/backend-ca.key',
         '-sha256','-days','3650','-subj','/CN=Warpgate Internal CA',
         '-addext','basicConstraints=critical,CA:TRUE',
         '-addext','keyUsage=critical,keyCertSign,cRLSign',
         '-out',root/'public/backend.crt'],**quiet)
    csr=root/'secrets/backend.csr'
    run(['openssl','req','-new','-key',root/'data/backend.key','-subj','/CN=localhost','-out',csr],**quiet)
    ext=root/'secrets/backend.ext'
    write(ext,'subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
    run(['openssl','x509','-req','-in',csr,'-CA',root/'public/backend.crt',
         '-CAkey',root/'secrets/backend-ca.key','-set_serial',str(secrets.randbits(120)),
         '-sha256','-days','825','-extfile',ext,'-out',root/'data/backend.crt'],**quiet)
    (root/'data/backend.key').chmod(0o600)
    (root/'public/backend.crt').chmod(0o644)
    csr.unlink();ext.unlink()

def initialize(binary, root, s, password, key, identity=None):
    """Real application initialization; tests call in their OWN temp root.

    Optional POSIX identity is supplied by the Linux installer to drop privileges.
    There is no production CLI flag that bypasses server/platform guards.
    """
    cfg,caddy=render(s,root)
    write(root/'config/warpgate.yaml',json.dumps(cfg,indent=2)+'\n')
    write(root/'public/Caddyfile',caddy,0o644)
    privilege={}
    if identity:
        uid,gid=identity
        for directory in ('config','data'):
            for p in [root/directory,*list((root/directory).rglob('*'))]:
                os.chown(p,uid,gid)
        privilege={'user':uid,'group':gid,'extra_groups':[]}
    env={**os.environ,'HOME':str(root/'data'),'WARPGATE_ENCRYPTION_KEY':key}
    # Explicit env deletion prevents accidental inherited debug/admin credentials.
    for name in ('WARPGATE_ADMIN_TOKEN','WARPGATE_ADMIN_PASSWORD','WARPGATE_ENCRYPTION_KEY_OLD'):
        env.pop(name,None)
    base=[str(binary),'--config',str(root/'config/warpgate.yaml')]
    with (root/'secrets/bootstrap.log').open('wb') as log:
        os.chmod(root/'secrets/bootstrap.log',0o600)
        run(base+['unattended-setup','--if-config-exists','bootstrap',
            '--data-path',str(root/'data'),'--http-port',str(s['backend_port']),
            '--listen-address','127.0.0.1','--external-host',s['domain'],
            '--record-sessions','--host-key-verification','auto-reject'],
            env={**env,'WARPGATE_ADMIN_PASSWORD':password},stdout=log,stderr=log,**privilege)
        token=secrets.token_urlsafe(48)
        proc=subprocess.Popen(base+['run','--enable-admin-token'],
            env={**env,'WARPGATE_ADMIN_TOKEN':token},stdout=log,stderr=log,**privilege)
        try:
            deadline=time.monotonic()+45
            while True:
                if proc.poll() is not None:
                    raise RuntimeError('Bootstrap server exited; inspect root-only bootstrap.log')
                try:
                    api(s['backend_port'],root/'public/backend.crt',token,'GET','/parameters')
                    break
                except (OSError,urllib.error.URLError):
                    if time.monotonic()>deadline: raise
                    time.sleep(.2)
            policy=load(PROJECT/'config/hardening.json')
            api(s['backend_port'],root/'public/backend.crt',token,'PUT','/parameters',policy)
            actual=api(s['backend_port'],root/'public/backend.crt',token,'GET','/parameters')
            for k,v in policy.items():
                if not matches(actual.get(k),v): raise RuntimeError(f'Hardening setting was not applied: {k}')
            keys=api(s['backend_port'],root/'public/backend.crt',token,'GET','/ssh/own-keys')
            ed=next(k for k in keys if k['public_key'].startswith('ssh-ed25519 '))
            write(root/'public/gateway-ed25519.pub',ed['public_key']+'\n',0o644)
            write(root/'public/gateway-key-id.txt',ed['id']+'\n',0o644)
        finally:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait()

def require_vps(flag):
    if not flag or platform.system()!='Linux' or os.geteuid()!=0:
        raise ValueError('Requires Linux VPS, root and explicit --on-vps')

def download_asset(asset, destination):
    # Fixed official URLs and SHA256 from versions.lock.json, no curl | shell.
    digest=hashlib.sha256()
    with urllib.request.urlopen(asset['url'],timeout=120) as src, destination.open('wb') as dst:
        while True:
            chunk=src.read(1024*1024)
            if not chunk:break
            digest.update(chunk);dst.write(chunk)
    if digest.hexdigest()!=asset['sha256']: raise ValueError('Release SHA256 mismatch')

def install(args):
    require_vps(args.on_vps)
    s=site_config(args.site)
    if ROOT.exists(): raise ValueError(f'{ROOT} exists; refusing to overwrite an existing/partial deployment')
    for name in ('warpgate-gateway.service','warpgate-https.service'):
        if (Path('/etc/systemd/system')/name).exists(): raise ValueError(f'Conflicting systemd unit: {name}')
    for name in ('wg-gateway','wg-https'):
        try: pwd.getpwnam(name)
        except KeyError: pass
        else: raise ValueError(f'Account {name} already exists; inspect before installing')
    for port in (80,443,s['backend_port']):
        with socket.socket() as sock: sock.bind(('0.0.0.0',port))
    addresses={item[4][0] for item in socket.getaddrinfo(s['domain'],None,socket.AF_INET)}
    if s['public_ip'] not in addresses: raise ValueError('Domain A record does not match site.public_ip')
    for command in ('systemctl','useradd','openssl','tailscale'):
        if not shutil.which(command): raise ValueError(f'Missing {command}; see docs/VPS.md')
    status=json.loads(subprocess.check_output(['tailscale','status','--json']))
    if status.get('BackendState')!='Running': raise ValueError('VPS must be logged into Tailscale first')
    password=getpass.getpass('Initial admin password (16+ chars, upper/lower/digit/symbol): ')
    if password!=getpass.getpass('Repeat admin password: '): raise ValueError('Passwords do not match')
    if len(password)<16 or not all(re.search(p,password) for p in ('[A-Z]','[a-z]','[0-9]','[^A-Za-z0-9]')):
        raise ValueError('Password does not meet the configured policy')
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if not arch: raise ValueError('Supported VPS architectures: x86_64/aarch64')
    lock=load(PROJECT/'config/versions.lock.json')
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        download_asset(lock['warpgate'][arch],tmp/'warpgate')
        download_asset(lock['caddy'][arch],tmp/'caddy.tar.gz')
        with tarfile.open(tmp/'caddy.tar.gz') as archive:
            with archive.extractfile('caddy') as source, (tmp/'caddy').open('wb') as dest:
                shutil.copyfileobj(source,dest)
        for name in ('wg-gateway','wg-https'):
            run(['useradd','--system','--user-group','--home-dir','/nonexistent','--no-create-home','--shell','/sbin/nologin',name])
        for directory in ('bin','config','data','secrets','public','caddy/data','caddy/config'):
            (ROOT/directory).mkdir(parents=True,exist_ok=True)
        ROOT.chmod(0o755);(ROOT/'secrets').chmod(0o700);(ROOT/'data').chmod(0o700)
        for name in ('warpgate','caddy'):
            shutil.copyfile(tmp/name,ROOT/'bin'/name);(ROOT/'bin'/name).chmod(0o755)
    key=base64.b64encode(secrets.token_bytes(32)).decode()
    write(ROOT/'secrets/encryption.env',f'WARPGATE_ENCRYPTION_KEY={key}\n')
    certificate(ROOT)
    account=pwd.getpwnam('wg-gateway')
    initialize(ROOT/'bin/warpgate',ROOT,s,password,key,(account.pw_uid,account.pw_gid))
    https=pwd.getpwnam('wg-https')
    for p in [ROOT/'caddy',*list((ROOT/'caddy').rglob('*'))]: os.chown(p,https.pw_uid,https.pw_gid)
    run([ROOT/'bin/caddy','validate','--config',ROOT/'public/Caddyfile','--adapter','caddyfile'])
    # Install dormant units only. No public listener until the operator starts it.
    for name in ('warpgate-gateway.service','warpgate-https.service'):
        dest=Path('/etc/systemd/system')/name
        if dest.exists(): raise ValueError(f'Conflicting systemd unit: {dest}')
        shutil.copyfile(PROJECT/'config'/name,dest)
    run(['systemctl','daemon-reload'])
    print('VPS_PREPARED. No public listener has been started. Follow docs/VPS.md for enrollment and activation.')
    print('Gateway public key: /opt/warpgate-gateway/public/gateway-ed25519.pub')

def prepare_target(args):
    r=load(args.receipt)
    for name in ('mac_tailnet_ip','vps_tailnet_ip'):
        addr=ipaddress.ip_address(r[name])
        if addr not in ipaddress.ip_network('100.64.0.0/10'): raise ValueError(f'{name} must be a Tailscale IPv4 address')
    if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}',r['username']): raise ValueError('Invalid Mac username')
    parts=r['host_public_key'].split()
    if len(parts)<2 or parts[0]!='ssh-ed25519': raise ValueError('Trusted Mac Ed25519 host key required')
    base64.b64decode(parts[1],validate=True)
    import uuid
    key_id=str(uuid.UUID(Path(args.key_id).read_text().strip()))
    out=Path(args.output)
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    known={'host':r['mac_tailnet_ip'],'port':22,'key_type':parts[0],'key_base64':parts[1]}
    target={'name':'remote-mac','require_approval':False,'ticket_requests_disabled':True,'ticket_require_approval':True,
        'options':{'kind':'Ssh','host':r['mac_tailnet_ip'],'port':22,'username':r['username'],
                   'allow_insecure_algos':False,'auth':{'kind':'PublicKey','key_id':key_id}}}
    write(out/'known-host.json',json.dumps(known,indent=2)+'\n')
    write(out/'target.json',json.dumps(target,indent=2)+'\n')
    print('Import the known host first, then create the SSH target in Admin UI; see docs/VPS.md.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('install');a.add_argument('--on-vps',action='store_true');a.add_argument('--site',required=True)
    a=sub.add_parser('render');a.add_argument('--site',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('prepare-target');a.add_argument('--receipt',required=True);a.add_argument('--key-id',required=True);a.add_argument('--output',required=True)
    args=p.parse_args()
    if args.command=='install': install(args)
    elif args.command=='prepare-target': prepare_target(args)
    else:
        s=site_config(args.site);out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
        cfg,caddy=render(s,ROOT)
        write(out/'warpgate.yaml',json.dumps(cfg,indent=2)+'\n')
        write(out/'Caddyfile',caddy)
        print('Rendered configuration only; no services or secrets created.')

if __name__=='__main__':
    try: main()
    except (ValueError,RuntimeError) as exc: raise SystemExit(str(exc))
