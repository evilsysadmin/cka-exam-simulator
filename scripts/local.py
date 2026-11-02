#!/usr/bin/env python3
"""Owned local lifecycle; Docker + Compose + Python + Make are the only host tools."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local/local'
CLUSTER = 'cka-local'
NETWORK = 'cka-local-cluster'
NODE = CLUSTER+'-control-plane'
NODE_IMAGE = 'kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f'
FIXTURE = 'ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine@sha256:e234d4f7e346c098f7b22245690372382aded5be988611eb2c53ae62231eece7'
HASHES = {'x86_64':('amd64','eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa'),
          'aarch64':('arm64','8e1014e87c34901cc422a1445866835d1e666f2a61301c27e722bdeab5a1f7e4')}
URL = 'http://127.0.0.1:8042'

def call(args, *, output=False, timeout=600, env=None):
    result = subprocess.run(args,cwd=ROOT,env=env,check=True,text=True,
                            stdout=subprocess.PIPE if output else None,timeout=timeout)
    return result.stdout.strip() if output else ''

def environment():
    # Never inherit remote Docker contexts, provider overrides or cloud credentials.
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','XDG_RUNTIME_DIR','LANG','TERM','DOCKER_HOST'}}
    host=env.get('DOCKER_HOST','unix:///var/run/docker.sock')
    if not host.startswith('unix:///'):
        raise ValueError('A local Unix Docker socket is required; remote Docker is refused')
    env['DOCKER_HOST']=host
    env['KIND_EXPERIMENTAL_PROVIDER']='docker'
    env['KIND_EXPERIMENTAL_DOCKER_NETWORK']=NETWORK
    env.update(CKA_LOCAL_UID=str(os.getuid()),CKA_LOCAL_GID=str(os.getgid()))
    return env

def source():
    # Content identity also works for a source export and uncommitted local edits.
    digest=hashlib.sha1()
    for folder in ['backend/cka_sim','go','frontend/src','deploy','scenarios']:
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and not any(p in {'__pycache__','node_modules'} for p in path.parts):
                digest.update(str(path.relative_to(ROOT)).encode()+b'\0'+path.read_bytes())
    for name in ['frontend/package.json','frontend/local.html','frontend/vite.local.config.ts','compose.local.yml']:
        digest.update((ROOT/name).read_bytes())
    return digest.hexdigest()

def compose(env, *args):
    return call(['docker','compose','-f','compose.local.yml',*args],env=env)

def kind(env):
    arch,checksum=HASHES[platform.machine()]
    binary=STATE/'kind'
    if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest()!=checksum:
        temporary=STATE/'kind.download'
        try:
            with urllib.request.urlopen('https://github.com/kubernetes-sigs/kind/releases/download/v0.31.0/kind-linux-'+arch,timeout=60) as response:
                with temporary.open('wb') as target:shutil.copyfileobj(response,target)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=checksum:
                raise ValueError('kind checksum mismatch')
            temporary.chmod(0o700);temporary.replace(binary)
        finally:
            temporary.unlink(missing_ok=True)
    return str(binary)

def nodes(env):
    return call(['docker','ps','-aq','--filter','label=io.x-k8s.kind.cluster='+CLUSTER],output=True,env=env).splitlines()

def owned_node(env):
    ids=nodes(env)
    if not ids:return False
    if len(ids)!=1 or not (STATE/'owner.json').is_file():raise ValueError('Refusing to adopt an existing cluster')
    info=json.loads(call(['docker','inspect',ids[0]],output=True,env=env))[0]
    owner=json.loads((STATE/'owner.json').read_text())
    if (info['Name']!='/'+NODE or info['Id']!=owner['node_id'] or info['Config']['Image']!=NODE_IMAGE
            or set(info['NetworkSettings']['Networks'])!={NETWORK}):
        raise ValueError('Cluster identity changed; refusing mutation')
    return True

def idle():
    path=STATE/'state/journal/leases.json'
    if path.exists():
        value=json.loads(path.read_text())
        if value.get('version')!=1 or value.get('entries')!=[]:
            raise ValueError('Finish the active attempt in the browser before rebuilding or stopping')

def network(env):
    ids=call(['docker','network','ls','-q','--filter','name=^'+NETWORK+'$'],env=env,output=True).splitlines()
    if not ids:
        call(['docker','network','create','--internal','--label','org.shadowops.cka=local',NETWORK],env=env)
    else:
        value=json.loads(call(['docker','network','inspect',NETWORK],env=env,output=True))[0]
        if value.get('Labels',{}).get('org.shadowops.cka')!='local' or not value.get('Internal'):
            raise ValueError('Refusing to adopt a foreign Docker network')

def main(action):
    if platform.system()!='Linux' or platform.machine() not in HASHES or os.getuid()==0:
        raise ValueError('Run as your ordinary user on Linux amd64/arm64 with Docker access')
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if STATE.is_symlink() or STATE.stat().st_uid!=os.getuid():raise ValueError('Private local state required')
    STATE.chmod(0o700)
    env=environment()
    # A single lifecycle operation at a time; this never locks the browser session.
    with (STATE/'operator.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        env['CKA_LOCAL_BUILD']=(STATE/'build').read_text().strip() if (STATE/'build').exists() else source()
        if action=='open':webbrowser.open(URL);return
        call(['docker','info','--format','{{.OSType}}'],env=env,output=True)
        call(['docker','compose','version'],env=env,output=True)
        if action=='logs':compose(env,'logs','--tail','100','app');return
        if action=='status':
            print('cluster_owned='+str(owned_node(env)).lower())
            compose(env,'ps')
            try:
                with urllib.request.urlopen(URL+'/readyz',timeout=3) as response: print('ready='+str(response.status==200).lower())
            except OSError:print('ready=false')
            print(URL);return
        if action=='down':
            idle(); present=owned_node(env)
            compose(env,'down')
            if present:call([kind(env),'delete','cluster','--name',CLUSTER],env=env)
            # Delete only the verified dedicated empty network.
            network(env)
            call(['docker','network','rm',NETWORK],env=env)
            (STATE/'owner.json').unlink(missing_ok=True)
            print('CKA_LOCAL_STOPPED');return
        if action not in {'up','update'}:raise ValueError('Use up|update|status|logs|open|down')
        idle()
        if action=='update':
            if call(['git','status','--porcelain'],output=True):raise ValueError('Commit or stash local changes before make update')
            call(['git','pull','--ff-only'])
            # Read the updated lifecycle code on the next invocation.
            os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/local.py'),'up'])
        binary=kind(env)
        network(env)
        present=owned_node(env)
        if not present:
            # Reserve all but one advertised CPU, even on large workstations,
            # so the excessive-CPU scenario starts Pending on every machine.
            cpus=int(call(['docker','info','--format','{{.NCPU}}'],env=env,output=True))
            if cpus<2:raise ValueError('Docker needs at least two CPUs and 4 GiB memory')
            config=STATE/'kind.yaml'
            config.write_text('kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnetworking:\n  apiServerAddress: 127.0.0.1\nnodes:\n- role: control-plane\n  kubeadmConfigPatches:\n  - |\n    kind: KubeletConfiguration\n    kubeReserved:\n      cpu: "'+str(cpus-1)+'"\n')
            try:
                call([binary,'create','cluster','--name',CLUSTER,'--image',NODE_IMAGE,'--config',str(config),'--kubeconfig',str(STATE/'kubeconfig'),'--wait','180s','--retain'],env=env)
            except subprocess.SubprocessError:
                ids=nodes(env)
                if len(ids)==1:
                    info=json.loads(call(['docker','inspect',ids[0]],env=env,output=True))[0]
                    if info['Name']=='/'+NODE and info['Config']['Image']==NODE_IMAGE and set(info['NetworkSettings']['Networks'])=={NETWORK}:
                        (STATE/'owner.json').write_text(json.dumps({'node_id':info['Id']}))
                        if os.environ.get('GITHUB_ACTIONS')=='true':call(['docker','logs','--tail','100',NODE],env=env)
                raise
            ids=nodes(env)
            if len(ids)!=1:raise ValueError('Expected one dedicated node')
            info=json.loads(call(['docker','inspect',ids[0]],env=env,output=True))[0]
            (STATE/'owner.json').write_text(json.dumps({'node_id':info['Id']}))
            owned_node(env)
            call(['docker','update','--cpus','2','--memory','3g','--memory-swap','3g','--pids-limit','1024',NODE],env=env)
        (STATE/'kubeconfig').chmod(0o600)
        call(['docker','pull',FIXTURE],env=env)
        # The trusted fixture uses this digest-qualified name.
        alias='localhost/cka-local-fixture:'+FIXTURE.split('@sha256:')[1]
        call(['docker','tag',FIXTURE,alias],env=env)
        call([binary,'load','docker-image',alias,'--name',CLUSTER],env=env)
        for target in [FIXTURE,FIXTURE.split(':1.27-alpine@')[0]+'@'+FIXTURE.split('@')[1]]:
            call(['docker','exec',NODE,'ctr','--namespace=k8s.io','images','tag','--force',alias,target],env=env)
        actual=json.loads(call(['docker','exec',NODE,'crictl','inspecti',FIXTURE],env=env,output=True))['status']['id']
        expected=json.loads(call(['docker','image','inspect',FIXTURE],env=env,output=True))[0]['Id']
        if actual!=expected:raise ValueError('Fixture digest mismatch')
        env['CKA_LOCAL_BUILD']=source()
        (STATE/'state').mkdir(mode=0o700,exist_ok=True)
        compose(env,'build','app')
        # Real GNU nano confinement is checked before starting the local app.
        compose(env,'run','--rm','--no-deps','--entrypoint','python','app','-c','from cka_sim.nano_sandbox import probe; probe()')
        (STATE/'build').write_text(env['CKA_LOCAL_BUILD'])
        compose(env,'up','-d','--wait','--wait-timeout','120','app')
        print('CKA_LOCAL_READY '+URL)

if __name__=='__main__':
    try:main(sys.argv[1] if len(sys.argv)==2 else '')
    except (ValueError,OSError,subprocess.SubprocessError,KeyError) as error:
        print('CKA_LOCAL_BLOCKED: '+str(error),file=sys.stderr);sys.exit(1)
