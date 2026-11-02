"""Disposable CI: real nano in the exact readonly image, no credentials/network."""
import subprocess

PROBE = 'from cka_sim.nano_sandbox import probe; probe()\n'


def main():
    command=['docker','run','--rm','-i','--user=0:0','--read-only','--network=none',
        '--cap-drop=ALL','--security-opt=no-new-privileges:true','--pids-limit=32',
        '--memory=128m','--memory-swap=128m','--cpus=0.1',
        '--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=8m','--entrypoint=python','cka-lab-ci','-']
    result=subprocess.run(command,input=PROBE,text=True,capture_output=True,timeout=120)
    if result.returncode or result.stdout.strip()!='CKA_REAL_NANO_SANDBOX_IMAGE_OK':
        print(result.stdout[-2000:]);print(result.stderr[-3000:])
        raise SystemExit('Real nano readonly image proof failed')
    print(result.stdout.strip())


if __name__=='__main__':main()
