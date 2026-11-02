"""Run against make up's real local API and dedicated cluster. No cloud access."""
import json
import subprocess
import time
import urllib.request
import urllib.error

URL='http://127.0.0.1:8042'
def post(path, body, token=None):
    headers={'Content-Type':'application/json','Origin':URL}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(URL+path,data=json.dumps(body,separators=(',',':')).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
def operation(action,body={}):
    deadline=time.monotonic()+15
    while True:
        token=post('/api/local/token',{})['token']
        try:return post('/api/labs/'+action,body,token)
        except urllib.error.HTTPError as error:
            # The serialized controller intentionally returns 409 while its
            # background reaper holds the actor lock. No operation ran.
            if error.code!=409 or time.monotonic()>=deadline:raise
            time.sleep(0.1)
def main():
    cap=operation('capabilities')
    assert cap['capacity']==1 and cap['current'] is None
    commands={
        'fix-catalog-service':'k patch svc catalog-api-svc --type=merge -p \'{"spec":{"selector":{"app":"catalog-api"}}}\'',
        'scale-payment-api':'k scale deployment payment-api --replicas=3',
        'recover-scheduler-capacity':'k set resources deployment batch-api --containers=web --requests=cpu=10m --limits=cpu=250m',
    }
    for scenario in cap['scenarios']:
        attempt=operation('prepare',{'slug':scenario['slug']})
        identity=attempt['session']['id']
        try:
            for action in ['up','down']:
                blocked=subprocess.run(['make',action],capture_output=True,text=True,timeout=15)
                assert blocked.returncode and 'Finish the active attempt' in blocked.stderr
            grade=operation('grade',{'id':identity})
            assert not grade['passed'],scenario['slug']
            operation('command',{'id':identity,'command':commands[scenario['slug']]})
            for _ in range(45):
                grade=operation('grade',{'id':identity})
                if grade['passed']:break
                time.sleep(2)
            assert grade['passed'],(scenario['slug'],grade)
            print('CKA_LOCAL_REAL_SCENARIO_OK '+scenario['slug'])
        finally:operation('finish',{'id':identity})
    assert operation('capabilities')['current'] is None
    print('CKA_LOCAL_REAL_CLEANUP_OK')
    # An unclean API exit must recover its journal and free the owned namespace;
    # it must not leave make up permanently blocked by an offline attempt.
    attempt=operation('prepare',{'slug':'fix-catalog-service'})
    subprocess.run(['docker','kill','cka-local-app-1'],check=True)
    subprocess.run(['make','up'],check=True)
    assert operation('capabilities')['current'] is None
    print('CKA_LOCAL_CRASH_RECOVERY_OK')
if __name__=='__main__':main()
