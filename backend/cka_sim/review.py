"""Read-only operator review queue. Never touches the curated scenario bank."""
import json
import re
from pathlib import Path

from .catalog import PROPOSALS
from .quality import audit_proposals
from .verification import REPORT

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z][a-z0-9-]{1,42}[a-z0-9]\.yaml\Z")


def _report_entries(path: Path) -> dict | None:
    """Reject untrusted, incomplete, or foreign-context verification records."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            return None
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = report["entries"]
        if (report.get("schema_version") != 1
                or report.get("passed") is not True
                or report.get("requires_human_review") is not True
                or report.get("cluster_context") != "kind-cka-dev"
                or not isinstance(rows, list) or not 1 <= len(rows) <= 5
                or type(report.get("verified")) is not int
                or type(report.get("total")) is not int
                or report["total"] != len(rows)
                or report["verified"] != len(rows)):
            return None
        result = {}
        for row in rows:
            name, digest = row["file"], row["sha256"]
            if (not isinstance(name, str) or not _NAME.fullmatch(name)
                    or name in result or row["status"] != "needs-human-review"
                    or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                    or row["scenario_type"] not in ("deployment_scale", "service_selector")):
                return None
            result[name] = row
        return result
    except (OSError, ValueError, UnicodeError, TypeError, KeyError, AttributeError):
        return None


def review_queue(*, directory: Path = PROPOSALS, evidence: Path = REPORT) -> dict:
    """Recheck current offline policy and exact bytes before human review."""
    if directory.is_symlink() or directory.resolve() != PROPOSALS.resolve():
        raise ValueError("Review requires the fixed quarantine")
    audit = audit_proposals(directory)
    records = _report_entries(evidence)
    accepted = [row for row in audit["entries"] if row["status"] == "accepted"]
    same_files = (audit["passed"] and records is not None
                  and set(records) == {row["file"] for row in accepted})
    rows = []
    for candidate in audit["entries"]:
        row = {"file": candidate["file"]}
        if candidate["status"] != "accepted":
            row.update(status="blocked", reason=candidate["reason"])
        elif not same_files:
            row["status"] = "needs-live-verification"
        elif (records[candidate["file"]]["sha256"] != candidate["sha256"]
              or records[candidate["file"]]["scenario_type"] != candidate["scenario_type"]):
            row["status"] = "stale-evidence"
        else:
            row.update(status="needs-human-review", sha256=candidate["sha256"],
                       scenario_type=candidate["scenario_type"])
        rows.append(row)
    return {"schema_version": 1, "requires_human_review": True,
            "ready": sum(r["status"] == "needs-human-review" for r in rows),
            "total": len(rows), "entries": rows}
