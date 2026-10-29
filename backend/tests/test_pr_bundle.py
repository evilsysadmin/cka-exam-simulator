"""A PR bundle is a local candidate, never a published scenario."""
import json

import pytest

from cka_sim.catalog import load_bank, save_generated
from cka_sim.models import Scenario
from cka_sim.pr_bundle import prepare_pr_bundle
from cka_sim.review import review_queue
from cka_sim.review_drafts import prepare_review_draft
from cka_sim.verification import save_report, verify_batch


def setup_bundle(monkeypatch, tmp_path):
    directory = tmp_path / "generated"
    data = load_bank()["scale-payment-api"].model_dump(mode="json")
    data.update(slug="candidate-for-pr", namespace="cka-candidate-for-pr",
                story="Recover replicas for this cluster before planned maintenance.")
    save_generated(Scenario.model_validate_json(json.dumps(data)), directory)
    for name in ("verification", "review", "review_drafts"):
        monkeypatch.setattr(f"cka_sim.{name}.PROPOSALS", directory)
    evidence = tmp_path / "evidence.json"
    save_report(verify_batch(directory=directory, smoke=lambda _: None), evidence)
    digest = review_queue(directory=directory, evidence=evidence)["entries"][0]["sha256"]
    drafts = tmp_path / "drafts"
    prepare_review_draft(slug="candidate-for-pr", approved_sha256=digest,
                         reviewed_by="operator-1", directory=directory,
                         evidence=evidence, drafts=drafts)
    args = dict(slug="candidate-for-pr", directory=directory, evidence=evidence,
                drafts=drafts, exports=tmp_path / "exports")
    return args


def test_approved_packet_exports_curated_json_but_never_publishes(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    output = prepare_pr_bundle(**args)
    s = Scenario.model_validate_json((output / "candidate-for-pr.json").read_text())
    assert s.slug == "candidate-for-pr"
    assert "human" in (output / "PR_REVIEW.txt").read_text().lower()
    assert "NOT PUBLISHED" in (output / "PR_REVIEW.txt").read_text()
    assert not (args["directory"].parent / "candidate-for-pr.json").exists()
    with pytest.raises(FileExistsError):
        prepare_pr_bundle(**args)


def test_packet_tamper_rejected(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    p = args["drafts"] / "candidate-for-pr.review.json"
    packet = json.loads(p.read_text())
    packet["scenario"]["target_replicas"] = 4
    p.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="does not match"):
        prepare_pr_bundle(**args)
    assert not args["exports"].exists()


def test_stale_verified_yaml_rejected(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    source = args["directory"] / "candidate-for-pr.yaml"
    source.write_text(source.read_text() + "\n# modified\n")
    with pytest.raises(ValueError):
        prepare_pr_bundle(**args)
    assert not args["exports"].exists()


def test_invalid_packet_status_fails_closed(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    p = args["drafts"] / "candidate-for-pr.review.json"
    packet = json.loads(p.read_text())
    packet["status"] = "approved-and-published"
    p.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="status"):
        prepare_pr_bundle(**args)


def test_report_context_change_invalidates_bundle(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    report = json.loads(args["evidence"].read_text())
    report["cluster_context"] = "kind-other"
    save_report(report, args["evidence"])
    with pytest.raises(ValueError):
        prepare_pr_bundle(**args)


def test_missing_and_symlink_drafts_are_rejected(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    p = args["drafts"] / "candidate-for-pr.review.json"
    p.unlink()
    with pytest.raises(ValueError, match="unsafe review"):
        prepare_pr_bundle(**args)
    destination = tmp_path / "outside.json"
    destination.write_text("{}")
    p.symlink_to(destination)
    with pytest.raises(ValueError, match="unsafe review"):
        prepare_pr_bundle(**args)


def test_symlink_export_directory_does_not_write_outside(monkeypatch, tmp_path):
    args = setup_bundle(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    args["exports"].symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepare_pr_bundle(**args)
    assert not list(outside.iterdir())


def test_rejects_path_traversal_slug_without_reading_files(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        prepare_pr_bundle(slug="../../run", exports=tmp_path / "exports")
