#!/usr/bin/env python3
"""Boot the actual lab image offline with synthetic Identity configuration.

Prove real Uvicorn starts, readiness stays denied with no Kubernetes, and
candidate endpoints do not bypass the strict Host/Origin boundary.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROBE = """
import json, time, urllib.error, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = "http://127.0.0.1:8080"
def get(path, token=None, origin=None):
    headers = {"Host": "cka-labs-staging.shadowops.dpdns.org"}
    if token is not None:
        headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(base + path, headers=headers,
        method="POST" if token is not None else "GET",
        data=b"{}" if token is not None else None)
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
for _ in range(90):
    try:
        code, live = get("/livez")
        if code == 200:
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.5)
else:
    raise AssertionError("actual lab API did not start")
assert live == {"status": "ok", "mode": "restricted-lab-pilot"}
code, status = get("/readyz")
assert (code, status) == (503, {"status": "reconciling"})
code, release = get("/api/labs/release")
assert code == 200 and release["build"] == __BUILD__
code, _ = get("/api/labs/capabilities", token="synthetic-not-signed",
              origin="https://staging.cka.shadowops.dpdns.org")
assert code == 503, code
code, _ = get("/api/labs/capabilities", token="synthetic-not-signed",
              origin="https://untrusted.invalid")
assert code == 403, code
print("CKA_LAB_IMAGE_FAIL_CLOSED_BOOT_OK")
"""


def docker(*args, timeout=30, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check, timeout=timeout)


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise RuntimeError("Disposable Linux GitHub runner only")
    build = os.environ["GITHUB_SHA"]
    if len(build) != 40 or any(c not in "0123456789abcdef" for c in build):
        raise RuntimeError("Immutable source SHA required")
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    keys = {"keys": [{"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
                      "kid": "ci-only", "x": base64.urlsafe_b64encode(public).rstrip(b"=").decode()}]}
    synthetic = {"environment": "staging", "jwks": keys,
                 "service_credential": "soc1_" + "A" * 43,
                 "pilot_subject": "11111111-1111-4111-8111-111111111111",
                 "build": build}
    name = "cka-lab-boot-" + str(os.getpid())
    with tempfile.TemporaryDirectory(prefix="cka-image-boot-") as directory:
        config = Path(directory) / "synthetic.json"
        config.write_text(json.dumps(synthetic), encoding="utf-8")
        # Test-only random public key and zero-value fake service credential.
        config.chmod(0o644)
        started = False
        try:
            docker("run", "--detach", "--rm", "--name", name,
                   "--network", "none", "--read-only", "--user", "65532:65532",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                   "--pids-limit", "64", "--memory", "256m", "--memory-swap", "256m",
                   "--cpus", "0.1",
                   "--tmpfs", "/var/lib/cka-lab:rw,nosuid,nodev,size=32m,mode=0700,uid=65532,gid=65532",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m",
                   "--mount", "type=bind,source=" + str(config) + ",target=/run/secrets/cka-lab-staging.json,readonly",
                   "--env", "CKA_LAB_BUILD_SHA=" + build, "cka-lab-ci")
            started = True
            proof = PROBE.replace("__BUILD__", repr(build))
            result = docker("exec", name, "python", "-c", proof, timeout=95)
            if result.stdout.strip() != "CKA_LAB_IMAGE_FAIL_CLOSED_BOOT_OK":
                raise RuntimeError("Unexpected controller boot proof output")
            print(result.stdout.strip())
        finally:
            if started:
                docker("rm", "--force", name, check=False)


if __name__ == "__main__":
    main()
