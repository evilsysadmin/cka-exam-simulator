"""Drafting is human-initiated, hash-gated, private and never publishes."""
import json
import stat

import pytest

from cka_sim.catalog import load_bank, save_generated
from cka_sim.models import Scenario
from cka_sim.review import review_queue
from cka_sim.review_drafts import prepare_review_draft
from cka_sim.verification import save_report, verify_batch


def setup_proposal(monkeypatch, tmp_path):
    folder = tmp_path / "generated"
    data = load_bank()["scale-payment-api"].model_dump(mode="json")
    data.update(slug="review-approved", namespace="cka-review-approved",
                story="Fix extra replicas before the holiday traffic spike.")
    save_generated(Scenario.model_validate_json(json.dumps(data)), folder)
    for module in ("verification", "review", "review_drafts"):
        monkeypatch.setattr(f"cka_sim.{module}.PROPOSALS", folder)
    evidence = tmp_path / "verification.json"
    save_report(verify_batch(directory=folder, smoke=lambda _: None), evidence)
    return folder, evidence


def draft(monkeypatch, tmp_path, **overrides):
    folder, evidence = setup_proposal(monkeypatch, tmp_path)
    digest = review_queue(directory=folder, evidence=evidence)["entries"][0]["sha256"]
    args = dict(slug="review-approved", approved_sha256=digest,
                reviewed_by="operator-1", directory=folder, evidence=evidence,
                drafts=tmp_path / "private-drafts")
    args.update(overrides)
    return folder, evidence, args


def test_explicit_review_creates_private_nonpublishing_packet(monkeypatch, tmp_path):
    folder, _, args = draft(monkeypatch, tmp_path)
    packet_path = prepare_review_draft(**args)
    packet = json.loads(packet_path.read_text())
    assert packet["status"] == "pending-separate-git-pr"
    assert packet["reviewer_label_unverified"] == "operator-1"
    assert packet["source_sha256"] == args["approved_sha256"]
    assert packet["scenario"]["slug"] == "review-approved"
    assert stat.S_IMODE(packet_path.stat().st_mode) == 0o600
    assert not (folder.parent / "review-approved.json").exists()
    with pytest.raises(FileExistsError):
        prepare_review_draft(**args)


@pytest.mark.parametrize("changed", [
    {"approved_sha256": "0" * 64},
    {"approved_sha256": "invalid"},
    {"reviewed_by": ""},
    {"reviewed_by": "../operator"},
    {"slug": "../other"},
    {"slug": "unknown-scenario"},
])
def test_invalid_or_mismatched_approval_cannot_create_packet(monkeypatch, tmp_path, changed):
    _, _, args = draft(monkeypatch, tmp_path, **changed)
    with pytest.raises(ValueError):
        prepare_review_draft(**args)
    assert not args["drafts"].exists()


def test_changed_source_requires_new_verification(monkeypatch, tmp_path):
    folder, _, args = draft(monkeypatch, tmp_path)
    source = folder / "review-approved.yaml"
    source.write_text(source.read_text() + "\n# edited since smoke\n")
    with pytest.raises(ValueError):
        prepare_review_draft(**args)
    assert not args["drafts"].exists()


def test_foreign_context_evidence_never_authorizes_packet(monkeypatch, tmp_path):
    _, evidence, args = draft(monkeypatch, tmp_path)
    record = json.loads(evidence.read_text())
    record["cluster_context"] = "kind-untrusted"
    save_report(record, evidence)
    with pytest.raises(ValueError):
        prepare_review_draft(**args)


def test_existing_symlink_output_directory_is_refused(monkeypatch, tmp_path):
    _, _, args = draft(monkeypatch, tmp_path)
    destination = tmp_path / "outside"
    destination.mkdir()
    args["drafts"].symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_review_draft(**args)
    assert list(destination.iterdir()) == []
