"""Operator-only export of an approved local review packet for a future Git PR.

Never writes into the tracked curated scenario bank, runs git, or publishes.
"""
import json
from hashlib import sha256
from pathlib import Path

from .catalog import PROPOSALS, load_proposal
from .review import review_queue
from .review_drafts import DRAFTS, _REVIEWER, _SHA256, _SLUG
from .verification import REPORT

EXPORTS = Path(__file__).resolve().parents[2] / ".local" / "pr-bundles"
MAX_PACKET_BYTES = 32_768


def prepare_pr_bundle(*, slug: str, directory: Path = PROPOSALS,
                      evidence: Path = REPORT, drafts: Path = DRAFTS,
                      exports: Path = EXPORTS) -> Path:
    """Stage curated JSON for a human-created PR only after all gates still pass."""
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ValueError("Invalid proposal slug")
    packet_path = drafts / (slug + ".review.json")
    if (drafts.is_symlink() or packet_path.is_symlink()
            or not packet_path.is_file()
            or packet_path.stat().st_size > MAX_PACKET_BYTES):
        raise ValueError("Missing or unsafe review packet")
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid review packet") from None
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version", "status", "reviewer_label_unverified",
        "source_sha256", "scenario",
    }:
        raise ValueError("Unexpected review packet structure")
    digest = packet["source_sha256"]
    reviewer = packet["reviewer_label_unverified"]
    if (type(packet["schema_version"]) is not int or packet["schema_version"] != 1
            or packet["status"] != "pending-separate-git-pr"
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or not isinstance(reviewer, str) or not _REVIEWER.fullmatch(reviewer)):
        raise ValueError("Review packet status or metadata invalid")
    queue = review_queue(directory=directory, evidence=evidence)
    row = next((r for r in queue["entries"] if r["file"] == slug + ".yaml"), None)
    if (row is None or row["status"] != "needs-human-review"
            or row["sha256"] != digest):
        raise ValueError("Review evidence missing or stale")

    source = directory / (slug + ".yaml")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Proposal is not a regular file")
    if sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("Proposal changed since review")
    scenario = load_proposal(source)
    if scenario.slug != slug or packet["scenario"] != scenario.model_dump(mode="json"):
        raise ValueError("Review packet does not match the verified proposal")
    if sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("Proposal changed while exporting")

    if exports.is_symlink() or exports.parent.is_symlink():
        raise ValueError("Export directory cannot be a symlink")
    exports.mkdir(parents=True, exist_ok=True)
    folder = exports / slug
    folder.mkdir(mode=0o700)  # Fail if already exists; never overwrite prior exports.
    try:
        # This is the only curated-format JSON written, outside the tracked bank.
        curated = folder / (slug + ".json")
        curated.write_text(
            json.dumps(scenario.model_dump(mode="json"),
                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        instructions = folder / "PR_REVIEW.txt"
        instructions.write_text(
            "REQUIRES SEPARATE HUMAN-REVIEWED GIT PR - NOT PUBLISHED\n"
            f"Proposal: {slug}\nSource SHA-256: {digest}\n"
            f"Operator label (not authenticated): {reviewer}\n\n"
            f"Candidate file: {slug}.json\n"
            f"After independently reviewing this file, manually copy it to "
            f"scenarios/{slug}.json on a new branch.\n"
            "Run validate-bank and the real Kubernetes lab-smoke for the slug.\n"
            "Open a Git PR and require all CI checks and human review.\n",
            encoding="utf-8")
        if source.is_symlink() or sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError("Proposal changed during export")
    except BaseException:
        for path in (folder / (slug + ".json"), folder / "PR_REVIEW.txt"):
            path.unlink(missing_ok=True)
        folder.rmdir()
        raise
    return folder
