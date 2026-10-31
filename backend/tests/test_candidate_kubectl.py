"""Candidate grammar must permit the actual repairs and prevent scope escape."""
import json
import unittest
from cka_sim.candidate_kubectl import plan_candidate_command, CandidateCommandError

NS = "cka-lab-" + "a" * 32
DEP = "payment-api"


def plan(command):
    return plan_candidate_command(command, namespace=NS, deployment=DEP, service=DEP + "-svc")


class CandidateKubectl(unittest.TestCase):
    def test_all_three_curated_repairs_use_the_fixed_namespace_and_resource(self):
        self.assertEqual(plan("kubectl scale deploy/payment-api --replicas=3"),
                         ["scale", "deployment/payment-api", "--replicas=3", "--namespace", NS])
        cpu = plan("kubectl set resources deployment/payment-api --containers=web --requests=cpu=10m --limits=cpu=250m")
        self.assertIn("--requests=cpu=10m", cpu)
        self.assertIn("--limits=cpu=250m", cpu)
        patch = plan("kubectl patch service payment-api-svc --type=merge -p '{\"spec\":{\"selector\":{\"app\":\"payment-api\"}}}'")
        self.assertEqual(json.loads(patch[patch.index("--patch") + 1]), {"spec": {"selector": {"app": "payment-api"}}})
        for command in [cpu, patch]:
            self.assertEqual(command[-2:], ["--namespace", NS])

    def test_read_only_diagnostics_and_aliases_are_supported(self):
        for command in ["kubectl get pods", "kubectl get deploy,svc -o wide", "kubectl describe pod payment-api-abc",
                        "kubectl get events --sort-by=.lastTimestamp", "kubectl get pods -l app=payment-api -o=json",
                        "kubectl rollout status deploy/payment-api", "kubectl explain deployment.spec.replicas"]:
            with self.subTest(command=command):
                self.assertEqual(plan(command)[-2:], ["--namespace", NS])

    def test_matching_explicit_namespace_is_normalized(self):
        expected = ["get", "pods", "--namespace", NS]
        for command in [f"kubectl -n {NS} get po", f"kubectl get pods --namespace={NS}", f"kubectl get pods -n={NS}"]:
            self.assertEqual(plan(command), expected)

    def test_namespace_credentials_files_proxy_and_cluster_scope_escapes_deny(self):
        for command in ["kubectl get pods -A", "kubectl get nodes", "kubectl get secrets", "kubectl get namespaces",
                        "kubectl get pods --all-namespaces", "kubectl get pods -n kube-system", "kubectl get pods -nkube-system",
                        f"kubectl -n {NS} get pods --namespace={NS}", "kubectl get pods --context=production",
                        "kubectl get pods --kubeconfig=/etc/config", "kubectl get pods --as=admin",
                        "kubectl get pods --token=secret", "kubectl get --raw /api/v1/secrets",
                        "kubectl get pods --server=https://metadata", "kubectl get pods --v=9",
                        "kubectl get pods -o=jsonpath={.items}", "kubectl apply -f /etc/passwd",
                        "kubectl exec payment-api -- sh", "kubectl run arbitrary --image=evil", "kubectl auth can-i '*' '*'"]:
            with self.subTest(command=command), self.assertRaises(CandidateCommandError):
                plan(command)

    def test_shell_syntax_multiline_and_oversized_commands_deny(self):
        for command in ["sh", "kubectl get pods; id", "kubectl get pods | sh", "kubectl get pods\nwhoami",
                        "kubectl get pods $(id)", "kubectl get pods `id`", "kubectl get pods > /tmp/file",
                        "kubectl get pods && id", "kubectl get pods '", "x" * 601, "kubectl get pods\0"]:
            with self.subTest(command=command), self.assertRaises(CandidateCommandError):
                plan(command)

    def test_mutations_cannot_target_other_workloads_or_change_pod_code_or_privileges(self):
        for command in ["kubectl scale deploy/other --replicas=3", "kubectl scale deploy/payment-api --replicas=100",
                        "kubectl set image deploy/payment-api web=evil", "kubectl set resources deploy/payment-api --containers=other --limits=cpu=250m",
                        "kubectl set resources deploy/payment-api --limits=cpu=17000m",
                        "kubectl set resources deploy/payment-api --requests=cpu=10m,memory=1Ti",
                        "kubectl patch svc payment-api-svc --patch-file=/tmp/patch --type=merge",
                        "kubectl patch svc other-svc --type=merge -p '{}'"]:
            with self.subTest(command=command), self.assertRaises(CandidateCommandError):
                plan(command)
        for patch in [{"spec": {"type": "LoadBalancer"}}, {"spec": {"externalIPs": ["169.254.169.254"]}},
                      {"spec": {"selector": {"app": "safe", "other": "unsafe"}}},
                      {"spec": {"template": {"spec": {"hostNetwork": True}}}}]:
            command = "kubectl patch svc payment-api-svc --type=merge -p '" + json.dumps(patch) + "'"
            with self.subTest(patch=patch), self.assertRaises(CandidateCommandError):
                plan(command)

    def test_duplicate_patch_keys_and_json_type_confusion_deny(self):
        for patch in ['{"spec":{"selector":{"app":"good","app":"evil"}}}',
                      '{"spec":{"replicas":true}}', '{"spec":{"replicas":3.0}}', '[]', 'null']:
            target = "deploy payment-api" if "replicas" in patch else "svc payment-api-svc"
            with self.subTest(patch=patch), self.assertRaises(CandidateCommandError):
                plan("kubectl patch " + target + " --type=merge -p '" + patch + "'")

    def test_controller_must_supply_an_actual_random_lease_scope(self):
        for namespace in ["default", "cka-payments", "cka-lab-short", "cka-lab-" + "A" * 32]:
            with self.assertRaises(CandidateCommandError):
                plan_candidate_command("kubectl get pods", namespace=namespace, deployment=DEP, service=DEP + "-svc")


if __name__ == "__main__":
    unittest.main()
