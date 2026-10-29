"""A verified scenario enters review; changes require re-verification."""
import json

from cka_sim.catalog import load_bank, save_generated
from cka_sim.models import Scenario
from cka_sim.review import review_queue
from cka_sim.verification import save_report, verify_batch


def setup(monkeypatch, tmp_path):
    folder = tmp_path / "generated"
    data = load_bank()["scale-payment-api"].model_dump(mode="json")
    data.update(slug="human-review-demo", namespace="cka-human-review",
                story="Repair service capacity before the winter traffic rush.")
    save_generated(Scenario.model_validate_json(json.dumps(data)), folder)
    monkeypatch.setattr("cka_sim.verification.PROPOSALS", folder)
    monkeypatch.setattr("cka_sim.review.PROPOSALS", folder)
    evidence = tmp_path / "verification.json"
    save_report(verify_batch(directory=folder, smoke=lambda _: None), evidence)
    return folder, evidence


def test_review_queue_requires_matching_live_evidence(monkeypatch, tmp_path):
    folder, evidence = setup(monkeypatch, tmp_path)
    result = review_queue(directory=folder, evidence=evidence)
    assert result["ready"] == result["total"] == 1
    assert result["requires_human_review"] is True
    assert result["entries"][0]["status"] == "needs-human-review"
    assert not (folder.parent / "human-review-demo.json").exists()


def test_changed_yaml_invalidates_evidence(monkeypatch, tmp_path):
    folder, evidence = setup(monkeypatch, tmp_path)
    path = folder / "human-review-demo.yaml"
    path.write_text(path.read_text().replace("minutes: 6", "minutes: 7"))
    result = review_queue(directory=folder, evidence=evidence)
    assert result["ready"] == 0
    assert result["entries"][0]["status"] == "stale-evidence"
