"""Real process bounds and namespace ownership/RBAC policy behavior."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

from cka_sim.leased_kube import (ClusterUnavailable, LeasedCluster, OWNER_LABEL,
                                 MANAGED_LABEL, namespace_policy, run_bounded)


ID = "a" * 32
NS = "cka-lab-" + "b" * 32


class PrivateDriver(unittest.TestCase):
    def test_policy_preserves_cpu_exercise_and_restricts_write_targets(self):
        objects = namespace_policy(ID, NS, "payment-api")
        namespace, account, quota, role, binding = objects
        self.assertEqual(namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")
        self.assertFalse(account["automountServiceAccountToken"])
        self.assertEqual(quota["spec"]["hard"]["secrets"], "0")
        self.assertEqual(quota["spec"]["hard"]["services.nodeports"], "0")
        self.assertNotIn("requests.cpu", quota["spec"]["hard"])
        writes = [r for r in role["rules"] if "patch" in r["verbs"]]
        self.assertEqual([r["resourceNames"] for r in writes], [["payment-api"], ["payment-api-svc"]])
        self.assertTrue(all("*" not in r["resources"] and "create" not in r["verbs"] for r in role["rules"]))
        self.assertEqual(binding["subjects"][0]["namespace"], NS)

    def test_foreign_or_invalid_scope_never_builds_policy(self):
        for lease, namespace, deployment in [("x", NS, "app"), (ID, "default", "app"),
                                             (ID, NS, "../../app"), (True, NS, "app")]:
            with self.subTest(namespace=namespace), self.assertRaises(ClusterUnavailable):
                namespace_policy(lease, namespace, deployment)

    def test_cleanup_does_not_adopt_a_namespace_with_foreign_uid_or_labels(self):
        value = {"kind": "Namespace", "metadata": {"name": NS,
                 "uid": "11111111-1111-4111-8111-111111111111",
                 "labels": {OWNER_LABEL: ID, MANAGED_LABEL: "staging-v1"}}}
        self.assertEqual(LeasedCluster._owned(value, ID, NS), value["metadata"]["uid"])
        for lease, namespace in [("c" * 32, NS), (ID, "cka-lab-" + "d" * 32)]:
            with self.assertRaises(ClusterUnavailable):
                LeasedCluster._owned(value, lease, namespace)
        value["metadata"]["labels"][MANAGED_LABEL] = "foreign"
        with self.assertRaises(ClusterUnavailable):
            LeasedCluster._owned(value, ID, NS)

    def test_process_accepts_bounded_streamed_input_and_output(self):
        code = 'import sys;sys.stdout.write(sys.stdin.read().upper())'
        self.assertEqual(run_bounded([sys.executable, "-c", code], input_text="fixture"), "FIXTURE")

    def test_hung_or_oversized_children_are_terminated_without_private_errors(self):
        for code in ['import time;time.sleep(20)',
                     'import sys;sys.stdout.write("x"*200000)',
                     'import sys;sys.stderr.write("secret"*1000)',
                     'import sys;sys.stderr.write("private credential");sys.exit(1)',
                     'import os,time\nif os.fork():os._exit(0)\ntime.sleep(20)']:
            started = time.monotonic()
            with self.subTest(code=code), self.assertRaises(ClusterUnavailable) as caught:
                run_bounded([sys.executable, "-c", code], timeout=0.2)
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("credential", str(caught.exception))
            self.assertLess(time.monotonic() - started, 2)

    def test_nonreading_stdin_is_also_deadline_bounded(self):
        started = time.monotonic()
        with self.assertRaises(ClusterUnavailable):
            run_bounded([sys.executable, "-c", "import time;time.sleep(20)"],
                        input_text="x" * 65536, timeout=0.2)
        self.assertLess(time.monotonic() - started, 2)

    def test_cleanup_absence_is_verified_and_foreign_namespaces_are_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            cluster = LeasedCluster(admin_config=Path(temporary) / "admin", scratch=Path(temporary))
            calls = []
            cluster._admin = lambda args, **kw: calls.append(args) or ""
            cluster.cleanup(ID, NS)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "get")
            calls.clear()
            cluster._admin = lambda args, **kw: calls.append(args) or '{"kind":"Namespace","metadata":{"name":"foreign"}}'
            with self.assertRaises(ClusterUnavailable):
                cluster.cleanup(ID, NS)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
