from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from cka_sim.local_api import create_local_app
from cka_sim.catalog import load_bank
from test_lab_api import Journal, Cluster

class LocalJournal(Journal):
    def close(self): pass

def test_local_requires_exact_peer_host_origin_and_nonce(tmp_path):
    (tmp_path/'index.html').write_text('local CKA')
    cluster=Cluster()
    app=create_local_app(leases=LocalJournal(),cluster=cluster,bank=load_bank(),build='a'*40,static=tmp_path)
    with TestClient(app,base_url='http://127.0.0.1:8042',client=('127.0.0.1',51000)) as client:
        assert client.get('/').status_code==200
        assert client.post('/api/local/token',json={}).status_code==403
        headers={'Origin':'http://127.0.0.1:8042'}
        for bad in ['https://evil.example','http://localhost:8042','null']:
            assert client.post('/api/local/token',json={},headers={'Origin':bad}).status_code==403
        assert client.post('/api/local/token',json={},headers={**headers,'Host':'evil.example:8042'}).status_code==403
        token=client.post('/api/local/token',json={},headers=headers).json()['token']
        assert client.post('/api/labs/capabilities',json={},headers=headers).status_code==401
        assert client.post('/api/labs/capabilities',json={},headers={**headers,'Authorization':'Bearer invalid'}).status_code==401
        headers['Authorization']='Bearer '+token
        # Other localhost applications' cookies never authorize or prevent use.
        headers['Cookie']='other_app=unrelated'
        assert client.post('/api/labs/capabilities',json={},headers=headers).json()['labs_enabled']
        attempt=client.post('/api/labs/prepare',json={'slug':'fix-catalog-service'},headers=headers).json()
        identity=attempt['session']['id']
        assert client.post('/api/labs/command',json={'id':identity,'command':'k get po | grep Running'},headers=headers).status_code==200
        assert client.post('/api/labs/finish',json={'id':identity},headers=headers).status_code==200
        assert cluster.calls==['provision','command','cleanup']
        assert client.get('/release.json?ts=42').json()['mode']=='local'
    with TestClient(app,base_url='http://127.0.0.1:8042',client=('192.0.2.1',51000)) as remote:
        assert remote.post('/api/local/token',json={},headers={'Origin':'http://127.0.0.1:8042'}).status_code==403

def test_driver_refuses_ambient_and_unknown_context(tmp_path):
    from cka_sim.leased_kube import LeasedCluster, ClusterUnavailable
    assert LeasedCluster(context='kind-cka-local').context=='kind-cka-local'
    with pytest.raises(ClusterUnavailable):LeasedCluster(context='production')
