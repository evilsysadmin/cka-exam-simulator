"""CI-only behavioral oracle: compare Go snapshot grading to existing Python grader.

No Kubernetes, credentials, live API, code generation or publication.
Go implementation must return exact same check order, names, flags and score.
"""
import argparse
import json
from pathlib import Path

from cka_sim.catalog import load_bank
from cka_sim.grading import grade_state


def check(fixtures_path: Path, report_path: Path, bank_path: Path) -> None:
    samples = json.loads(fixtures_path.read_text(encoding="utf-8"))
    go_reports = json.loads(report_path.read_text(encoding="utf-8"))
    bank = load_bank(bank_path)
    if not isinstance(samples, list) or len(samples) < 25:
        raise ValueError("Insufficient snapshot coverage")
    if not isinstance(go_reports, list) or len(go_reports) != len(samples):
        raise ValueError("Go grading report has missing or extra cases")
    names = set()
    for sample, actual in zip(samples, go_reports, strict=True):
        name = sample["name"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Duplicate or invalid fixture name")
        names.add(name)
        if name != actual["name"]:
            raise AssertionError(f"Go fixture ordering differs for {name}")
        s = bank[sample["scenario_slug"]]
        expected = grade_state(
            s, deployment=sample["deployment"],
            service=sample.get("service"),
            endpoint_slices=sample.get("endpoint_slices"),
        ).as_dict()
        if expected["passed"] is not sample["expected_passed"]:
            raise AssertionError(f"Incorrect expected pass/fail in fixture {name}")
        if actual["grade"] != expected:
            raise AssertionError(
                f"Go/Python grading differs in {name}: "
                f"Go={actual['grade']!r} Python={expected!r}"
            )
    print(f"CKA_GO_PYTHON_GRADE_PARITY_OK snapshots={len(samples)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--go-report", type=Path, required=True)
    parser.add_argument("--bank", type=Path, required=True)
    args = parser.parse_args()
    check(args.fixtures, args.go_report, args.bank)
