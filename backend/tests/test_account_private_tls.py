"""Real certificate/hostname verification and RPC bytes against a local TLS fixture."""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
import pytest

from cka_sim.account_api import HOST, ORIGIN, create_account_app
from cka_sim.identity_transport import private_identity_tls, IDENTITY_HOST
from test_identity_freshness import material, signed


def certificates(directory, hostname):
    now=datetime.now(timezone.utc)
    root_key=ec.generate_private_key(ec.SECP256R1())
    root_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic account TLS CA')])
    root=(x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name).public_key(root_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(root_key,hashes.SHA256()))
    key=ec.generate_private_key(ec.SECP256R1())
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,hostname)]))
          .issuer_name(root_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]),critical=False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).sign(root_key,hashes.SHA256()))
    cert=directory/'cert.pem';private=directory/'key.pem'
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    private.chmod(0o600)
    return root.public_bytes(serialization.Encoding.PEM).decode(),cert,private


@pytest.mark.parametrize('hostname',[IDENTITY_HOST,'wrong-identity.invalid'])
def test_real_private_tls_and_still_unexpired_revocation(material,tmp_path,monkeypatch,hostname):
    ca,cert,key=certificates(tmp_path,hostname)
    state={'revoked':False,'calls':0}
    class Authority(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            state['calls']+=1
            assert self.path=='/api/identity/freshness'
            assert self.headers['Host']==IDENTITY_HOST
            assert self.headers['Authorization']=='Bearer soc1_'+'A'*43
            assert self.headers.get('Cookie') is None and self.headers.get('Origin') is None
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request=={'subject':material[2]['sub'],'session_version':material[2]['sv'],'token_jti':material[2]['jti']}
            body=json.dumps({**asdict(material[3]),'audience':'cka-simulator','checked_at':int(time.time())}).encode()
            self.send_response(401 if state['revoked'] else 200)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Authority)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    real=socket.getaddrinfo
    def resolver(host,port,*args,**kwargs):
        # Only the test DNS resolver changes. URL, Host and TLS SNI stay fixed.
        if host==IDENTITY_HOST:return real('127.0.0.1',server.server_port,*args,**kwargs)
        return real(host,port,*args,**kwargs)
    monkeypatch.setattr(socket,'getaddrinfo',resolver)
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    try:
        trust=private_identity_tls(ca)
        assert trust.verify_mode==ssl.CERT_REQUIRED and trust.check_hostname is True
        assert len(trust.get_ca_certs())==1
        app=create_account_app(jwks=material[1],credential='soc1_'+'A'*43,build='a'*40,tls_context=trust)
        api=TestClient(app,base_url='https://'+HOST)
        headers={'Origin':ORIGIN,'Authorization':'Bearer '+signed(material[0],material[2])}
        response=api.get('/api/account/me',headers=headers)
        if hostname!=IDENTITY_HOST:
            assert response.status_code==401 and state['calls']==0
        else:
            assert response.status_code==200 and response.json()['subject']==material[2]['sub']
            state['revoked']=True
            assert api.get('/api/account/me',headers=headers).status_code==401
            assert material[2]['exp']>time.time() and state['calls']==2
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_no_system_trust_or_disabled_hostname_verification():
    with pytest.raises((ValueError,ssl.SSLError,TypeError)):
        private_identity_tls('not a CA')
