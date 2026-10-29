"""Batch verification must fail closed, avoid exposing details and never publish."""
import json
from hashlib import sha256

import pytest

from cka_sim.catalog import PROPOSALS, load_bank, save_generated
from cka_sim.models import Scenario
from cka_sim.verification import save_report, verify_batch


def _proposal(*, slug="first-lab", namespace="cka-first-lab", story=None):
    data = load_bank()["scale-payment-api"].model_dump(mode="json")
    data.update(slug=slug, namespace=namespace,
                story=story or f"Restore service capacity safely for {slug} before peak load.")
    return Scenario.model_validate_json(json.dumps(data))


def _save(monkeypatch, tmp_path, scenarios):
    generated = tmp_path / "generated"
    for s in scenarios:
        save_generated(s, generated)
    monkeypatch.setattr("cka_sim.verification.PROPOSALS", generated)
    return generated


def test_valid_batch_requires_human_review_and_records_digest(monkeypatch, tmp_path):
    folder = _save(monkeypatch, tmp_path, [_proposal()])
    seen = []
    report = verify_batch(directory=folder, smoke=lambda s: seen.append(s.slug))
    assert seen == ["first-lab"]
    assert report["passed"] and report["verified"] == 1
    assert report["requires_human_review"] is True
    assert report["entries"][0]["status"] == "needs-human-review"
    assert report["entries"][0]["sha256"] == sha256(
        (folder / "first-lab.yaml").read_bytes()).hexdigest()
    assert "Restore service" not in json.dumps(report)
    report_path = tmp_path / ".local" / "verification.json"
    save_report(report, report_path)
    assert json.loads(report_path.read_text()) == report
    assert not (tmp_path / "scenarios" / "first-lab.json").exists()


def test_offline_rejection_blocks_every_kubernetes_call(monkeypatch, tmp_path):
    folder = _save(monkeypatch, tmp_path, [_proposal()])
    (folder / "invalid.yaml").write_text("invalid: [")
    called = []
    report = verify_batch(directory=folder, smoke=lambda s: called.append(s.slug))
    assert not report["passed"] and report["reason"] == "offline-audit-rejected"
    assert called == []
    assert {e["status"] for e in report["entries"]} == {"rejected", "blocked"}


def test_excessive_batch_refuses_to_use_cluster(monkeypatch, tmp_path):
    scenarios = [_proposal(slug=f"lab-{n}", namespace=f"cka-lab-{n}")
                 for n in range(6)]
    folder = _save(monkeypatch, tmp_path, scenarios)
    called = []
    report = verify_batch(directory=folder, smoke=lambda s: called.append(s.slug))
    assert not report["passed"]
    assert report["reason"] == "live-batch-limit-exceeded"
    assert called == [] and report["verified"] == 0


def test_cluster_failure_aborts_batch_and_sanitizes_error(monkeypatch, tmp_path):
    folder = _save(monkeypatch, tmp_path, [
        _proposal(slug="first-lab", namespace="cka-first-lab"),
        _proposal(slug="second-lab", namespace="cka-second-lab")])
    seen = []
    def fail(scenario):
        seen.append(scenario.slug)
        raise RuntimeError("PRIVATE_KUBERNETES_LOG_OR_MANIFEST")
    report = verify_batch(directory=folder, smoke=fail)
    assert seen == ["first-lab"]
    assert report["entries"][0]["status"] == "failed"
    assert report["entries"][1]["status"] == "blocked"
    assert "PRIVATE_KUBERNETES" not in json.dumps(report)


def test_file_change_during_smoke_invalidates_success(monkeypatch, tmp_path):
    folder = _save(monkeypatch, tmp_path, [_proposal()])
    def alter(_scenario):
        (folder / "first-lab.yaml").write_text("altered: true")
    report = verify_batch(directory=folder, smoke=alter)
    assert report["passed"] is False
    assert report["verified"] == 0
    assert report["entries"][0]["status"] == "failed"


def test_wrong_quarantine_or_symlink_refuses_to_run(monkeypatch, tmp_path):
    folder = _save(monkeypatch, tmp_path, [_proposal()])
    with pytest.raises(ValueError, match="fixed proposals"):
        verify_batch(directory=tmp_path, smoke=lambda s: None)
    link = tmp_path / "linked"
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match="fixed proposals"):
        verify_batch(directory=link, smoke=lambda s: None)


@pytest.mark.parametrize("limit", [0, -1, 6, 200])
def test_invalid_live_limits_rejected_before_running(limit):
    with pytest.raises(ValueError, match="limit"):
        verify_batch(smoke=lambda s: None, limit=limit)


def test_symlink_report_path_is_never_followed(tmp_path):
    outside = tmp_path / "private"
    outside.write_text("keep")
    report = tmp_path / "report.json"
    report.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        save_report({"passed": True}, report)
    assert outside.read_text() == "keep"
