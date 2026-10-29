"""Operator-only Kubernetes verification of quarantined scenarios.

Never invokes a provider, writes the curated bank, or authorizes publication.
Only a bounded, pre-audited batch may run against the dedicated kind context.
"""
import json
import os
from hashlib import sha256
from pathlib import Path
import tempfile
from typing import Callable

from .catalog import PROPOSALS, load_proposal
from .models import Scenario
from .quality import audit_proposals

MAX_LIVE_BATCH = 5
REPORT = Path(__file__).resolve().parents[2] / ".local" / "proposal-verification.json"


def verify_batch(*, smoke: Callable[[Scenario], None], directory: Path = PROPOSALS,
                 limit: int = MAX_LIVE_BATCH) -> dict:
    """Sequentially prove initial-fail and canonical-fix via an injected trusted smoke.

    Halt on first failure: a failed cleanup may have left a contaminated lab.
    Each passing file still needs independent human review and promotion.
    """
    if not 1 <= limit <= MAX_LIVE_BATCH:
        raise ValueError("Live verification limit must be between 1 and 5")
    if directory.is_symlink() or directory.resolve() != PROPOSALS.resolve():
        raise ValueError("Live verification requires the fixed proposals quarantine")

    audit = audit_proposals(directory)
    report = {
        "schema_version": 1, "passed": False, "requires_human_review": True,
        "cluster_context": "kind-cka-dev", "verified": 0,
        "total": audit["total"], "entries": [],
    }
    # Never launch Kubernetes until the complete quarantine passes offline policy.
    if not audit["passed"]:
        report["reason"] = "offline-audit-rejected"
        report["entries"] = [
            {"file": row["file"], "status":
             "rejected" if row["status"] == "rejected" else "blocked",
             **({"reason": row["reason"]} if row["status"] == "rejected" else {})}
            for row in audit["entries"]
        ]
        return report
    if audit["total"] > limit:
        report["reason"] = "live-batch-limit-exceeded"
        report["entries"] = [{"file": row["file"], "status": "blocked"}
                             for row in audit["entries"]]
        return report

    halted = False
    for row in audit["entries"]:
        item = {"file": row["file"], "sha256": row["sha256"],
                "scenario_type": row["scenario_type"]}
        if halted:
            item["status"] = "blocked"
            item["reason"] = "earlier-smoke-failed"
            report["entries"].append(item)
            continue
        path = directory / row["file"]
        try:
            # Re-read and revalidate to detect stale/modified inputs after the audit.
            if path.is_symlink() or not path.is_file():
                raise ValueError("Input changed")
            scenario = load_proposal(path)
            if sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Input changed")
            smoke(scenario)
            # A modified file must not retain a successful verification attestation.
            if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Input changed during Kubernetes verification")
        except Exception:
            # Never serialize Kubernetes errors or untrusted story, title, or manifests.
            item["status"] = "failed"
            item["reason"] = "kubernetes-or-input-verification-failed"
            halted = True
        else:
            item["status"] = "needs-human-review"
            report["verified"] += 1
        report["entries"].append(item)

    report["passed"] = report["verified"] == report["total"]
    return report


def save_report(report: dict, destination: Path = REPORT) -> None:
    """Save sanitized evidence atomically, private to this local working directory."""
    if destination.is_symlink():
        raise ValueError("Refusing to overwrite a symlink verification report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".verification-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
