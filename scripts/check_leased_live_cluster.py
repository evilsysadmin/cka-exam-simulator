"""Actual journal -> candidate RBAC -> repair -> verified namespace cleanup.

Runs only in the disposable CI-owned kind-cka-dev cluster, with an explicit
temporary context alias required by the staging driver. Never uses ambient config.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from cka_sim.catalog import load_bank
from cka_sim.lease_process import LeaseProcess, LeaseRejected
from cka_sim.leased_kube import ClusterUnavailable, LeasedCluster, run_bounded
from cka_sim.models import Scenario
from cka_sim.render import IMAGE

ROOT = Path(__file__).resolve().parents[1]
OWNER = "11111111-1111-4111-8111-111111111111"


def run(binary):
    kubeconfig = ROOT / ".local/kubeconfig"
    kubectl = Path(shutil.which("kubectl")).resolve()
    # Refuse every caller/context except the dedicated cluster created by CI.
    names = subprocess.check_output([str(kubectl), "--kubeconfig", str(kubeconfig),
                                    "config", "get-contexts", "-o", "name"], text=True).splitlines()
    assert names == ["kind-cka-dev"], names
    with tempfile.TemporaryDirectory(prefix="cka-leased-live-") as directory:
        private = Path(directory)
        admin = private / "admin.yaml"
        shutil.copyfile(kubeconfig, admin)
        admin.chmod(0o600)
        subprocess.run([str(kubectl), "--kubeconfig", str(admin), "config", "rename-context",
                        "kind-cka-dev", "kind-cka-lab-staging"], check=True, capture_output=True)
        cluster = LeasedCluster(admin_config=admin, binary=kubectl, scratch=private / "candidate")
        journal = private / "journal"
        journal.mkdir(mode=0o700)
        with LeaseProcess(journal=journal, bank=ROOT / "scenarios", binary=binary) as leases:
            for slug in load_bank():
                lease = leases.start(OWNER, slug)
                identity = lease["ID"]
                scenario = Scenario.model_validate_json(json.dumps(lease["Scenario"]))
                namespace = scenario.namespace
                try:
                    cluster.provision(identity, scenario)
                    assert not cluster.grade(identity, scenario)["passed"], slug
                    with cluster._candidate(identity, namespace) as base:
                        for args in [["get", "pods", "-n", "kube-system"],
                                     ["get", "secrets", "-n", namespace],
                                     ["get", "namespaces"],
                                     ["create", "token", "candidate", "-n", namespace],
                                     ["patch", "service", "foreign", "-n", namespace,
                                      "--type=merge", "-p", '{"spec":{"type":"NodePort"}}'],
                                     ["create", "-n", namespace, "-f", "-"]]:
                            try:
                                run_bounded(base + args, input_text=json.dumps({
                                    "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "escape"},
                                    "spec": {"containers": [{"name": "web", "image": IMAGE}]}}))
                            except ClusterUnavailable:
                                pass
                            else:
                                raise AssertionError("Candidate RBAC allowed a forbidden operation")
                    # Test real admission independently of candidate RBAC.
                    forbidden_pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {
                        "name": "privilege-probe", "namespace": namespace}, "spec": {
                            "hostNetwork": True, "containers": [{"name": "probe", "image": IMAGE,
                            "securityContext": {"privileged": True}, "resources": {
                                "requests": {"memory": "32Mi"}, "limits": {"memory": "128Mi"}}}]}}
                    result = subprocess.run([str(kubectl), "--kubeconfig", str(admin),
                            "--context", "kind-cka-lab-staging", "create", "-f", "-"],
                            input=json.dumps(forbidden_pod), text=True, capture_output=True, timeout=12)
                    assert result.returncode != 0 and "violates PodSecurity" in result.stderr
                    if scenario.scenario_type == "deployment_scale":
                        command = f"kubectl scale deployment/{scenario.deployment} --replicas={scenario.target_replicas}"
                    elif scenario.scenario_type == "deployment_resources":
                        command = f"kubectl set resources deployment/{scenario.deployment} --containers=web --requests=cpu=10m --limits=cpu=250m"
                    else:
                        patch = json.dumps({"spec": {"selector": {"app": scenario.target_selector}}})
                        command = f"kubectl patch service/{scenario.service} --type=merge -p '{patch}'"
                    cluster.command(identity, scenario, command)
                    deadline = time.monotonic() + 90
                    while not cluster.grade(identity, scenario)["passed"]:
                        assert time.monotonic() < deadline, slug + " did not become healthy"
                        time.sleep(1)
                finally:
                    cleanup = leases.finish(OWNER, identity)
                    # Capacity remains occupied until actual namespace absence.
                    try:
                        leases.start(OWNER, slug)
                    except LeaseRejected:
                        pass
                    else:
                        raise AssertionError("Dirty lease was reused")
                    cluster.cleanup(cleanup["ID"], cleanup["Namespace"])
                    leases.confirm_cleanup(cleanup["ID"], cleanup["Namespace"])
                    assert leases.pending() == []
                print("CKA_LEASED_LIVE_SCENARIO_OK " + slug, flush=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve())
