"""Offline audits are deterministic, fail closed and never publish candidates."""
import json

import pytest

from cka_sim.catalog import load_bank, save_generated
from cka_sim.models import Scenario
from cka_sim.quality import MAX_BATCH_FILES, audit_proposals


def proposed(*, slug="fresh-a", namespace="cka-fresh-a",
             story="The platform needs extra replicas before the billing rush."):
    source = load_bank()["scale-payment-api"].model_dump(mode="json")
    source.update(slug=slug, namespace=namespace, story=story)
    return Scenario.model_validate_json(json.dumps(source))


def test_empty_or_missing_quarantine_fails_cleanly(tmp_path):
    missing = audit_proposals(tmp_path / "absent")
    assert missing["passed"] is False
    assert missing["error"] == "missing-or-unsafe-directory"
    (tmp_path / "empty").mkdir()
    assert audit_proposals(tmp_path / "empty")["error"] == "no-proposals"


def test_accepts_valid_yaml_with_digest_but_never_exposes_text(tmp_path):
    directory = tmp_path / "generated"
    s = proposed()
    file = save_generated(s, directory)
    result = audit_proposals(directory)
    assert result["passed"] is True
    assert (result["total"], result["accepted"], result["rejected"]) == (1, 1, 0)
    entry = result["entries"][0]
    assert entry["status"] == "accepted"
    assert len(entry["sha256"]) == 64
    assert entry["scenario_type"] == "deployment_scale"
    assert s.story not in json.dumps(result)
    assert s.title not in json.dumps(result)
    assert file.exists()


def test_rejects_exact_normalized_duplicate_even_when_names_differ(tmp_path):
    directory = tmp_path / "generated"
    first = proposed()
    second = proposed(slug="fresh-b", namespace="cka-fresh-b",
                      story="THE platform needs extra replicas before the billing rush!!!")
    save_generated(first, directory)
    save_generated(second, directory)
    result = audit_proposals(directory)
    assert result["passed"] is False
    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert result["entries"][1]["reason"] == "duplicate-narrative"
    assert result["entries"][1]["duplicate_of"] == first.slug


def test_rejects_narrative_copied_from_curated_bank(tmp_path):
    directory = tmp_path / "generated"
    old = load_bank()["scale-payment-api"]
    save_generated(proposed(story=old.story), directory)
    entry = audit_proposals(directory)["entries"][0]
    assert entry["reason"] == "duplicate-narrative"
    assert entry["duplicate_of"] == old.slug


def test_invalid_yaml_cannot_pass_or_leak_payload(tmp_path):
    directory = tmp_path / "generated"
    directory.mkdir()
    secret = "DO-NOT-DISPLAY-RAW-LLM-TEXT"
    (directory / "bad.yaml").write_text("story: " + secret + "\nunknown: [")
    result = audit_proposals(directory)
    assert result["passed"] is False
    assert result["entries"][0]["reason"] == "invalid-schema-or-policy"
    assert secret not in json.dumps(result)


def test_rejects_symlink_and_non_yaml_file_without_reading_it(tmp_path):
    directory = tmp_path / "generated"
    directory.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    (directory / "escape.yaml").symlink_to(outside)
    (directory / "invalid.json").write_text("{}")
    result = audit_proposals(directory)
    assert result["rejected"] == 2
    assert all(e["reason"] == "unsupported-file" for e in result["entries"])
    assert "private" not in json.dumps(result)


def test_rejects_bounded_batch_limit_without_scanning_contents(tmp_path):
    directory = tmp_path / "generated"
    directory.mkdir()
    for index in range(MAX_BATCH_FILES + 1):
        (directory / f"{index:04}.yaml").write_text("not a scenario")
    result = audit_proposals(directory)
    assert result["error"] == "too-many-proposals"
    assert result["passed"] is False
    assert result["rejected"] == MAX_BATCH_FILES + 1


def test_can_detect_curated_slug_namespace_collisions_in_manually_dropped_yaml(tmp_path):
    directory = tmp_path / "generated"
    directory.mkdir()
    curated = load_bank()["scale-payment-api"]
    (directory / f"{curated.slug}.yaml").write_text(curated.model_dump_json())
    result = audit_proposals(directory)
    assert result["entries"][0]["reason"] == "curated-collision"


def test_different_scenario_type_does_not_reject_same_story(tmp_path):
    directory = tmp_path / "generated"
    a = proposed()
    b_data = a.model_dump(mode="json")
    b_data.update(
        slug="network-b", namespace="cka-network-b",
        scenario_type="service_selector",
        initial_selector="unused-legacy", target_selector="payment-api",
        initial_replicas=2, target_replicas=2,
    )
    b = Scenario.model_validate_json(json.dumps(b_data))
    save_generated(a, directory)
    save_generated(b, directory)
    assert audit_proposals(directory)["passed"] is True
