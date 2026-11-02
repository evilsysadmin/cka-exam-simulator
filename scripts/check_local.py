"""Run against make up's real local API and dedicated cluster. No cloud access."""
import json
import time
import urllib.request

URL='http://127.0.0.1:8042'
def post(path, body, token=None):
    headers={'Content-Type':'application/json','Origin':URL}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(URL+path,data=json.dumps(body,separators=(',',':')).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
def operation(action,body={}):
    token=post('/api/local/token',{})['token']
    return post('/api/labs/'+action,body,token)
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
if __name__=='__main__':main()
