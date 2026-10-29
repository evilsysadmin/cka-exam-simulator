"""Prove namespace-scoped RBAC and real-kind Go/Python grade parity.

Only the trusted Python test harness creates/solves/cleans a scenario and
installs a disposable namespace Role. Go uses a short-lived ServiceAccount
token with no write operations. Nothing is printed about token contents.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import yaml
from cka_sim import kube
from cka_sim.catalog import load_bank

REPO = Path(__file__).resolve().parents[1]
BIN = Path(sys.argv[1])
ADMIN_CONFIG = REPO / ".local" / "kubeconfig"
ACCOUNT = "cka-grade-observer"
CONTEXT = "kind-cka-dev"


def scoped_role(s):
    objects = [
        {"apiVersion": "v1", "kind": "ServiceAccount",
         "metadata": {"name": ACCOUNT, "namespace": s.namespace}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
         "metadata": {"name": ACCOUNT, "namespace": s.namespace},
         "rules": [
             {"apiGroups": ["apps"], "resources": ["deployments"],
              "resourceNames": [s.deployment], "verbs": ["get"]},
             {"apiGroups": [""], "resources": ["services"],
              "resourceNames": [s.service], "verbs": ["get"]},
             # RBAC cannot filter LIST by label. Isolation depends on an
             # exercise-specific namespace: don't share this namespace.
             {"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"],
              "verbs": ["list"]},
         ]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
         "metadata": {"name": ACCOUNT, "namespace": s.namespace},
         "subjects": [{"kind": "ServiceAccount", "name": ACCOUNT,
                       "namespace": s.namespace}],
         "roleRef": {"kind": "Role", "name": ACCOUNT,
                     "apiGroup": "rbac.authorization.k8s.io"}},
    ]
    kube.kubectl(["apply", "-f", "-"],
                 input_text=json.dumps({"apiVersion": "v1", "kind": "List",
                                        "items": objects}))


def mint_scoped_config(s, directory: Path) -> Path:
    # The privileged kubeconfig is used ONLY here in the trusted test harness
    # for TokenRequest; it is not forwarded to the Go observer.
    token = kube.kubectl(["create", "token", ACCOUNT, "-n", s.namespace,
                          "--duration=10m"]).strip()
    if not token:
        raise AssertionError("TokenRequest did not return an SA token")
    original = yaml.safe_load(ADMIN_CONFIG.read_text())
    original_clusters = [c for c in original["clusters"] if c["name"] == CONTEXT]
    if len(original_clusters) != 1:
        raise AssertionError("Dedicated kind cluster not found")
    kubeconfig = {
        "apiVersion": "v1", "kind": "Config",
        "current-context": CONTEXT,
        "clusters": original_clusters,
        "contexts": [{"name": CONTEXT, "context": {
            "cluster": CONTEXT, "user": CONTEXT, "namespace": s.namespace}}],
        "users": [{"name": CONTEXT, "user": {"token": token}}],
    }
    path = directory / "readonly-kubeconfig"
    path.write_text(yaml.safe_dump(kubeconfig), encoding="utf-8")
    path.chmod(0o600)
    return path


def verify_real_rbac(s, config: Path):
    def can_i(verb, resource, *, namespace=None, resource_name=None):
        args = ["kubectl", "--kubeconfig", str(config), "--context", CONTEXT,
                "auth", "can-i", verb, resource, "-n", namespace or s.namespace]
        if resource_name:
            args.append("--resource-name=" + resource_name)
        proc = subprocess.run(args, capture_output=True, text=True, timeout=25)
        if proc.returncode not in (0, 1) or proc.stdout.strip() not in ("yes", "no"):
            category = ("unknown-option" if "unknown flag" in proc.stderr
                        else "unauthorized" if "Unauthorized" in proc.stderr
                        else "forbidden" if "Forbidden" in proc.stderr
                        else "other-kubectl-error")
            raise AssertionError(
                f"RBAC SelfSubjectAccessReview failed: {verb} {resource} "
                f"exit={proc.returncode} stdout={proc.stdout.strip()[:24]!r} "
                f"category={category}"
            )
        return proc.stdout.strip() == "yes"

    # First ensure the actual bearer token can GET a named workload through
    # Kubernetes, rather than trusting SelfSubjectAccessReview alone.
    get_command = [
        "kubectl", "--kubeconfig", str(config), "--context", CONTEXT,
        "get", "deployment", s.deployment, "-n", s.namespace, "-o", "name",
    ]
    result = subprocess.run(get_command, capture_output=True, text=True, timeout=25)
    if result.returncode != 0 or result.stdout.strip() != f"deployment.apps/{s.deployment}":
        category = ("unauthorized" if "Unauthorized" in result.stderr
                    else "forbidden" if "Forbidden" in result.stderr
                    else "other-kubectl-error")
        raise AssertionError(f"Scoped bearer token GET failed, category={category}")

    for verb, resource, name in [
        ("get", "deployments.apps", s.deployment),
        ("get", "services", s.service),
        ("list", "endpointslices.discovery.k8s.io", None),
    ]:
        if not can_i(verb, resource, resource_name=name):
            raise AssertionError("Required scoped read unexpectedly denied")
    for verb, resource, ns, name in [
        ("create", "deployments.apps", s.namespace, None),
        ("update", "deployments.apps", s.namespace, s.deployment),
        ("patch", "deployments.apps", s.namespace, s.deployment),
        ("delete", "deployments.apps", s.namespace, s.deployment),
        ("get", "secrets", s.namespace, None),
        ("list", "pods", s.namespace, None),
        ("list", "deployments.apps", s.namespace, None),
        ("watch", "endpointslices.discovery.k8s.io", s.namespace, None),
        ("get", "deployments.apps", s.namespace, "another-workload"),
        ("get", "deployments.apps", "cka-catalog", s.deployment),
        ("get", "services", "default", s.service),
        ("list", "nodes", s.namespace, None),
    ]:
        if can_i(verb, resource, namespace=ns, resource_name=name):
            raise AssertionError(f"RBAC allowed forbidden {verb} on {resource}")


def go_grade(slug: str, config: Path) -> dict:
    result = subprocess.run(
        [str(BIN), "-dev-live", "-bank", str(REPO / "scenarios"),
         "-kubeconfig", str(config), "-slug", slug],
        check=True, capture_output=True, text=True, timeout=40,
    )
    return json.loads(result.stdout)


def until_ready(s, config: Path, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        python_grade = kube.grade(s)
        go_result = go_grade(s.slug, config)
        if python_grade["passed"] and go_result["passed"]:
            if python_grade != go_result:
                raise AssertionError("Go/Python grades differ on stable solved kind state")
            return
        time.sleep(2)
    raise AssertionError("Real-kind Go/Python observer did not both reach solved state")


def main():
    if not BIN.is_file() or not ADMIN_CONFIG.is_file():
        raise ValueError("Dedicated Go observer binary or kubeconfig missing")
    s = load_bank()["scale-payment-api"]
    kube.setup(s)
    try:
        scoped_role(s)
        with tempfile.TemporaryDirectory(prefix="cka-scoped-kind-") as root:
            config = mint_scoped_config(s, Path(root))
            verify_real_rbac(s, config)
            initial_python = kube.grade(s)
            initial_go = go_grade(s.slug, config)
            if initial_python["passed"] or initial_go["passed"]:
                raise AssertionError("Known broken kind fixture was graded solved")
            kube.solve_for_smoke(s)
            until_ready(s, config)
            print("CKA_GO_SCOPED_RBAC_AND_KIND_PARITY_OK scenario=scale-payment-api")
    finally:
        kube.cleanup(s)


if __name__ == "__main__":
    main()
