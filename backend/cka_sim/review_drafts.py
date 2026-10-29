"""Explicit operator review packets. This never publishes curated scenarios.

Local verification reports are not signed attestations; use only in a trusted
operator workspace. A separate reviewed Git PR is required for publication.
"""
import json
import os
import re
from hashlib import sha256
from pathlib import Path

from .catalog import PROPOSALS, load_proposal
from .review import review_queue
from .verification import REPORT

DRAFTS = Path(__file__).resolve().parents[2] / ".local" / "review-drafts"
_REVIEWER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z][a-z0-9-]{1,42}[a-z0-9]\Z")


def prepare_review_draft(*, slug: str, approved_sha256: str, reviewed_by: str,
                         directory: Path = PROPOSALS, evidence: Path = REPORT,
                         drafts: Path = DRAFTS) -> Path:
    """Make an immutable private draft after explicit, hash-bound human action."""
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ValueError("Invalid proposal slug")
    if not isinstance(approved_sha256, str) or not _SHA256.fullmatch(approved_sha256):
        raise ValueError("The exact reviewed SHA-256 is required")
    if not isinstance(reviewed_by, str) or not _REVIEWER.fullmatch(reviewed_by):
        raise ValueError("A simple operator label is required")

    queue = review_queue(directory=directory, evidence=evidence)
    row = next((item for item in queue["entries"]
                if item["file"] == slug + ".yaml"), None)
    if (row is None or row["status"] != "needs-human-review"
            or row["sha256"] != approved_sha256):
        raise ValueError("Proposal is unverified, stale, or hash does not match")

    source = directory / (slug + ".yaml")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Proposal is not a regular file")
    raw = source.read_bytes()
    if sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("Proposal changed")
    scenario = load_proposal(source)
    if scenario.slug != slug or sha256(source.read_bytes()).hexdigest() != approved_sha256:
        raise ValueError("Proposal changed during review")

    # This is intentionally NOT a curated scenario JSON object.
    packet = {
        "schema_version": 1,
        "status": "pending-separate-git-pr",
        "reviewer_label_unverified": reviewed_by,
        "source_sha256": approved_sha256,
        "scenario": scenario.model_dump(mode="json"),
    }
    if drafts.is_symlink() or drafts.parent.is_symlink():
        raise ValueError("Review directory cannot be a symlink")
    drafts.mkdir(parents=True, exist_ok=True)
    target = drafts / (slug + ".review.json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(packet, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if source.is_symlink() or sha256(source.read_bytes()).hexdigest() != approved_sha256:
            raise ValueError("Proposal changed while writing the draft")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target
