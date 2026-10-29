"""CI-only contract: Go candidate JSON must match Python without private fields.

No network, Kubernetes context, candidate terminal or inference.
"""
import argparse
import json
from pathlib import Path

from cka_sim.catalog import load_bank


PUBLIC_KEYS = frozenset({
    "slug", "title", "story", "difficulty", "minutes",
    "domain", "statement", "namespace",
})
PRIVATE_KEYS = frozenset({
    "schema_version", "deployment", "scenario_type", "initial_replicas",
    "target_replicas", "initial_selector", "target_selector",
    "solution", "grading", "manifest", "kubeconfig",
})


def check(bank_path: Path, report_path: Path):
    bank = load_bank(bank_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, list) or len(report) != len(bank):
        raise AssertionError("Go candidate projection has missing or extra scenarios")
    if len(bank) < 3:
        raise AssertionError("Candidate parity coverage unexpectedly shrank")
    expected = [bank[slug].candidate_view() for slug in sorted(bank)]
    if report != expected:
        raise AssertionError("Go candidate projections differ from Python's safe API contract")
    for entry in report:
        if not isinstance(entry, dict) or set(entry) != PUBLIC_KEYS:
            raise AssertionError("Go candidate projection exposes an unapproved field")
        if set(entry) & PRIVATE_KEYS:
            raise AssertionError("Go candidate projection contains private scenario data")
    print(f"CKA_GO_PYTHON_CANDIDATE_PARITY_OK scenarios={len(report)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--go-report", type=Path, required=True)
    args = parser.parse_args()
    check(args.bank, args.go_report)
