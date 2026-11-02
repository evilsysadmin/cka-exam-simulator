"""Actual restricted nano PTY, immutable manifests and fresh stream authority."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import yaml

from cka_sim.candidate_filters import split_filter
from cka_sim.candidate_kubectl import CandidateCommandError, plan_candidate_command
from cka_sim.lab_editor import NanoEditors, EditRejected, document, edit_patch, edit_target
from cka_sim.leased_kube import run_bounded
from cka_sim.lab_api import HOST, ORIGIN
from test_lab_api import setup, post
from test_identity_freshness import material

NS='cka-lab-'+'b'*32
SCENARIO=SimpleNamespace(namespace=NS,deployment='catalog-api',service='catalog-api-svc')
RESOURCE={'apiVersion':'v1','kind':'Service','metadata':{'name':SCENARIO.service,'namespace':NS,'resourceVersion':'11'},
          'spec':{'selector':{'app':'old'},'ports':[{'port':80,'targetPort':8080}]}}


def test_alias_and_grep_are_real_bounded_stdin_filters():
    assert plan_candidate_command('k get po',namespace=NS,deployment='catalog-api',service='catalog-api-svc') == ['get','pods','--namespace',NS]
    assert plan_candidate_command('k',namespace=NS,deployment='catalog-api',service='catalog-api-svc')[0]=='--help'
    command,grep=split_filter("k get po | grep -in 'running'")
    assert command=='k get po'
    assert run_bounded(grep,input_text='one Running\ntwo Pending\n',timeout=1,accepted_codes=(0,1))=='1:one Running\n'
    assert run_bounded(grep,input_text='no matches\n',timeout=1,accepted_codes=(0,1))==''
    for bad in ('k get po | sh', 'k get po | grep -r /run/secrets', 'k get po | grep -f /etc/passwd',
                'k get po | grep pattern /etc/passwd','k get po | grep x | sh','k get po | grep -FE x'):
        with pytest.raises(CandidateCommandError):split_filter(bad)


def test_editor_targets_only_owned_resources_not_paths_or_subcommands():
    for command in ('kubectl edit svc catalog-api-svc','k edit service/catalog-api-svc','nano svc/catalog-api-svc'):
        assert edit_target(command,SCENARIO)==('service',SCENARIO.service)
    for command in ('nano /run/secrets/cka-lab-admin.yaml','kubectl edit namespace default',
                    'k edit svc other','kubectl edit svc catalog-api-svc -n kube-system','k edit svc/catalog-api-svc;sh'):
        with pytest.raises(CandidateCommandError):edit_target(command,SCENARIO)


def test_saved_yaml_preserves_identity_ports_privileges_and_resource_version():
    updated=deepcopy(RESOURCE);updated['spec']['selector']['app']='new'
    assert edit_patch(RESOURCE,document(yaml.safe_dump(updated)))==[
        {'op':'test','path':'/metadata/resourceVersion','value':'11'},
        {'op':'replace','path':'/spec/selector/app','value':'new'}]
    assert edit_patch(RESOURCE,deepcopy(RESOURCE))==[]
    changes=[lambda x:x['metadata'].update(namespace='kube-system'),lambda x:x['metadata'].update(resourceVersion='12'),
             lambda x:x['spec']['ports'][0].update(targetPort=1),lambda x:x['spec'].update(externalIPs=['169.254.169.254']),
             lambda x:x.update(status={'secret':'other'})]
    for change in changes:
        bad=deepcopy(updated);change(bad)
        with pytest.raises(EditRejected):edit_patch(RESOURCE,bad)
    for raw in ('spec: {selector: {app: a, app: b}}','a: &x [*x]','!!python/object/apply:os.system [id]','x'*65537):
        with pytest.raises(EditRejected):document(raw)


def drain(manager,editor,seconds=.15):
    until=time.monotonic()+seconds;output=''
    while time.monotonic()<until:
        output+=manager.read(editor);time.sleep(.01)
    return output


def rewrite(raw):
    # Nano's real cut-line, write-out and exit keys, not a simulated editor.
    return '\x1b\\'+'\x0b'*(len(yaml.safe_dump(RESOURCE,sort_keys=False).splitlines())+2)+raw.replace('\n','\r')+'\x0f\r\x18'


def test_real_nano_can_save_and_exit_in_the_kernel_sandbox():
    manager=NanoEditors()
    try:
        opened=manager.open('owner','lease',SCENARIO,'k edit svc/catalog-api-svc',RESOURCE,120)
        editor=manager.owned('owner','lease',opened['editor'])
        manager.attach(editor,100,24)
        output='';ready=time.monotonic()+10
        while 'nano' not in output.lower() and time.monotonic()<ready and editor.process.poll() is None:
            output+=drain(manager,editor,.03)
        assert 'nano' in output.lower()
        updated=deepcopy(RESOURCE);updated['spec']['selector']['app']='new'
        manager.input(editor,rewrite(yaml.safe_dump(updated,sort_keys=False)))
        for _ in range(100):
            output+=drain(manager,editor,.03)
            if editor.process.poll() is not None:break
        assert editor.process.poll()==0,output[-200:]
        assert 'nano' in output.lower()
        assert manager.saved(editor)==updated
        assert edit_patch(RESOURCE,manager.saved(editor))[-1]['value']=='new'
        with pytest.raises(EditRejected):manager.owned('other','lease',opened['editor'])
        manager.resize(editor,80,20)
    finally:manager.close()
    assert not editor.directory.exists()


def test_kernel_sandbox_denies_private_files_execution_network_and_process_access(tmp_path):
    # No live credentials exist in this process. Still prove the kernel refuses
    # paths outside the sole editable directory and system read-only libraries.
    private=tmp_path/'private';private.write_text('synthetic-private-data')
    work=tmp_path/'work';work.mkdir()
    script='''import os,socket,subprocess,sys
from pathlib import Path
from cka_sim.nano_sandbox import confine
confine(Path(sys.argv[1]))
for action in (lambda:Path(sys.argv[2]).read_text(),lambda:Path('/proc/self/environ').read_text(),lambda:socket.socket(),lambda:subprocess.run(['/bin/sh','-c','id'])):
 try:action();raise SystemExit('isolation failed')
 except OSError:pass
(Path(sys.argv[1])/'allowed').write_text('ok')
print('NANO_KERNEL_CONFINEMENT_OK')
'''
    result=subprocess.run([sys.executable,'-c',script,str(work),str(private)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='NANO_KERNEL_CONFINEMENT_OK'
    assert (work/'allowed').read_text()=='ok'


def editor_api(material):
    api,headers,controller,journal,cluster,state=setup(material)
    controller.editors=NanoEditors()
    cluster.edit_snapshot=lambda *args:deepcopy(RESOURCE)
    cluster.edit_apply=lambda *args:cluster.calls.append(('edit-apply',args[-1]))
    identity=post(api,headers,'prepare',slug='fix-catalog-service').json()['session']['id']
    opened=post(api,headers,'edit-open',id=identity,command='kubectl edit svc/catalog-api-svc')
    assert opened.status_code==200,opened.text
    return api,headers,controller,identity,opened.json()['editor'],cluster,state


def test_real_editor_stream_and_save_require_fresh_owner_authority(material):
    api,headers,controller,identity,editor,cluster,state=editor_api(material)
    try:
        with api.websocket_connect('wss://'+HOST+'/api/labs/editor',headers={'Origin':ORIGIN}) as socket:
            socket.send_json({'type':'auth','id':identity,'editor':editor,'token':headers['Authorization'][7:],'columns':100,'rows':24})
            assert socket.receive_json()['type']=='output'
            updated=deepcopy(RESOURCE);updated['spec']['selector']['app']='new'
            socket.send_json({'type':'input','data':rewrite(yaml.safe_dump(updated,sort_keys=False))})
            for _ in range(200):
                frame=socket.receive_json()
                if frame['type']=='exit':
                    assert frame['code']==0;break
            else:raise AssertionError('nano never exited')
        result=post(api,headers,'edit-save',id=identity,editor=editor)
        assert result.status_code==200,result.text
        assert cluster.calls[-1][0]=='edit-apply'
        assert state['calls']>=4
        assert controller.editors.current is None
    finally:controller.editors.close()


def test_revocation_closes_the_pty_without_applying_a_saved_file(material):
    api,headers,controller,identity,editor,cluster,state=editor_api(material)
    try:
        with api.websocket_connect('wss://'+HOST+'/api/labs/editor',headers={'Origin':ORIGIN}) as socket:
            socket.send_json({'type':'auth','id':identity,'editor':editor,'token':headers['Authorization'][7:],'columns':80,'rows':24})
            assert socket.receive_json()['type']=='output'
            state['revoked']=True
            socket.send_json({'type':'auth','token':headers['Authorization'][7:]})
            for _ in range(30):
                if socket.receive_json()['type']=='error':break
        assert post(api,headers,'edit-save',id=identity,editor=editor).status_code==401
        assert not any(isinstance(call,tuple) and call[0]=='edit-apply' for call in cluster.calls)
    finally:controller.editors.close()


def test_editor_heartbeats_have_a_separate_bounded_budget(material):
    from dataclasses import asdict
    import httpx
    from cka_sim.lab_api import staging_authorizer
    from cka_sim.lab_controller import LabRateLimited
    from test_identity_freshness import signed
    def authority(request):
        return httpx.Response(200,json={**asdict(material[3]),'audience':'cka-simulator'})
    options=dict(jwks=material[1],credential='soc1_'+'A'*43,access_mode='authenticated-staging',transport=httpx.MockTransport(authority))
    commands=staging_authorizer(**options)
    editor=staging_authorizer(**options,editor_stream=True)
    token=signed(material[0],material[2])
    for _ in range(36):commands(token)
    with pytest.raises(LabRateLimited):commands(token)
    for _ in range(18):editor(token)
    with pytest.raises(LabRateLimited):editor(token)
