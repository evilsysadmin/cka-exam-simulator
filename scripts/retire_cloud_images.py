"""Actions-only retirement of this repository's two historical cloud packages."""
import json
import os
import urllib.error
import urllib.request

OWNER='evilsysadmin'
REPOSITORY='evilsysadmin/cka-exam-simulator'
PACKAGES=('cka-account-staging','cka-lab-staging')

def owned(value,name):
    return (value.get('name')==name and value.get('package_type')=='container'
            and value.get('repository',{}).get('full_name')==REPOSITORY)

def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_REPOSITORY')!=REPOSITORY:
        raise RuntimeError('Run the protected retirement workflow')
    token=os.environ['GH_TOKEN']
    def api(method,name):
        request=urllib.request.Request('https://api.github.com/users/'+OWNER+'/packages/container/'+name,
            method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                return json.load(response) if method=='GET' else None
        except urllib.error.HTTPError as error:
            if error.code==404:return None
            raise RuntimeError('Package API status '+str(error.code)) from None
    plan=[]
    for name in PACKAGES:
        value=api('GET',name)
        if value is not None:
            if not owned(value,name):raise RuntimeError('Package ownership mismatch')
            plan.append(name)
    for name in plan:api('DELETE',name)
    if any(api('GET',name) is not None for name in PACKAGES):raise RuntimeError('Cloud package remains')
    print('CKA_CLOUD_IMAGES_RETIRED packages=0 repository=preserved')
if __name__=='__main__':main()
