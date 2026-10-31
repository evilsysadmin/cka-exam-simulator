"""Bounded pipe failure tests and opt-in actual Go journal interoperability."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from cka_sim.lease_process import LeaseProcess, LeaseRejected, LeaseUnavailable


OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ROOT = Path(__file__).resolve().parents[2]


class PipeBoundary(unittest.TestCase):
    def helper(self, source, *, timeout=1.0):
        temp = tempfile.TemporaryDirectory(prefix="cka-lease-pipe-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        binary = root / "synthetic-peer"
        binary.write_text("#!/usr/bin/python3\n" + source)
        binary.chmod(0o700)
        peer = LeaseProcess(journal=root, bank=root, binary=binary, timeout=timeout)
        self.addCleanup(peer.close)
        return peer

    def test_preserves_one_private_process_for_multiple_requests(self):
        peer = self.helper('import json,sys\nfor line in sys.stdin:\n d=json.loads(line);print(json.dumps({"ok":True,"result":d["op"]}),flush=True)\n')
        self.assertEqual(peer.pending(), "pending")
        self.assertEqual(peer.start(OWNER, "scale-payment-api"), "start")
        self.assertEqual(peer.status(OWNER, "a" * 32), "status")

    def test_typed_denial_does_not_poison_a_healthy_controller(self):
        peer = self.helper('import json,sys\nfor line in sys.stdin:\n d=json.loads(line);print(json.dumps({"ok":False,"error":"denied"} if d["op"]=="status" else {"ok":True,"result":[]}),flush=True)\n')
        with self.assertRaises(LeaseRejected) as error:
            peer.status(OTHER, "a" * 32)
        self.assertEqual(error.exception.code, "denied")
        self.assertEqual(peer.pending(), [])

    def test_timeout_kills_peer_and_never_restarts_over_an_uncleaned_lease(self):
        peer = self.helper('import sys,time\nfor line in sys.stdin:time.sleep(20)\n', timeout=0.1)
        started = time.monotonic()
        with self.assertRaises(LeaseUnavailable):
            peer.pending()
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(peer._process.poll())
        with self.assertRaises(LeaseUnavailable):
            peer.pending()

    def test_truncated_oversized_or_ambiguous_replies_fail_closed(self):
        for reply in ["not JSON\n", '{"ok":true,"ok":false,"result":[]}\n',
                      '{"ok":true,"result":[]}\n{}\n', '{"ok":true,"result":[],"secret":"private"}\n',
                      '{"ok":false,"error":"private-detail"}\n', 'x' * 16385 + '\n',
                      '{"ok":true,"result":[]}']:
            with self.subTest(length=len(reply)):
                peer = self.helper('import sys\nsys.stdin.readline()\nsys.stdout.write(' + repr(reply) + ')\nsys.stdout.flush()\n')
                with self.assertRaises(LeaseUnavailable) as error:
                    peer.pending()
                self.assertNotIn("private", str(error.exception))
                self.assertIsNotNone(peer._process.poll())

    def test_journal_failure_poisoned_process_cannot_serve_again(self):
        peer = self.helper('import sys\nfor line in sys.stdin:print(\'{"ok":false,"error":"journal-unavailable"}\',flush=True)\n')
        with self.assertRaises(LeaseUnavailable):
            peer.pending()
        with self.assertRaises(LeaseUnavailable):
            peer.start(OWNER, "scale-payment-api")

    def test_untrusted_relative_paths_and_unbounded_deadlines_rejected(self):
        for changes in [{"journal": Path("relative")}, {"bank": Path("relative")},
                        {"binary": Path("relative")}, {"timeout": 0}, {"timeout": 20},
                        {"timeout": True}, {"timeout": float("nan")}]:
            args = {"journal": Path("/tmp"), "bank": Path("/tmp"), "binary": Path("/not-an-installed-binary")}
            args.update(changes)
            with self.assertRaises(LeaseUnavailable):
                LeaseProcess(**args)


@unittest.skipUnless(os.environ.get("CKA_LEASE_BINARY"), "requires an explicitly compiled Go helper")
class ActualGoJournal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cka-real-journal-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)

    def start_peer(self):
        peer = LeaseProcess(journal=self.directory, bank=ROOT / "scenarios", binary=Path(os.environ["CKA_LEASE_BINARY"]))
        self.addCleanup(peer.close)
        return peer

    def test_real_owner_namespace_capacity_and_cleanup_handshake(self):
        peer = self.start_peer()
        lease = peer.start(OWNER, "scale-payment-api")
        self.assertTrue(lease["Namespace"].startswith("cka-lab-"))
        self.assertEqual(peer.status(OWNER, lease["ID"])["phase"], "active")
        self.assertEqual(peer.scenario(OWNER, lease["ID"])["namespace"], lease["Namespace"])
        for who in [OWNER, OTHER]:
            with self.assertRaises(LeaseRejected):
                peer.start(who, "scale-payment-api")
        with self.assertRaises(LeaseRejected) as denied:
            peer.status(OTHER, lease["ID"])
        self.assertEqual(denied.exception.code, "denied")
        with self.assertRaises(LeaseRejected):
            peer.confirm_cleanup(lease["ID"], lease["Namespace"])
        cleanup = peer.finish(OWNER, lease["ID"])
        self.assertEqual(peer.pending(), [cleanup])
        self.assertTrue(peer.confirm_cleanup(cleanup["ID"], cleanup["Namespace"]))
        self.assertEqual(peer.pending(), [])
        self.assertTrue(peer.start(OTHER, "scale-payment-api")["ID"])

    def test_actual_restart_preserves_closed_cleanup_obligation(self):
        peer = self.start_peer()
        lease = peer.start(OWNER, "scale-payment-api")
        peer.close()
        recovered = self.start_peer()
        self.assertEqual(recovered.status(OWNER, lease["ID"])["phase"], "closed")
        with self.assertRaises(LeaseRejected):
            recovered.scenario(OWNER, lease["ID"])
        with self.assertRaises(LeaseRejected):
            recovered.start(OTHER, "scale-payment-api")
        self.assertEqual(recovered.pending(), [{"ID": lease["ID"], "Namespace": lease["Namespace"]}])


if __name__ == "__main__":
    unittest.main()
