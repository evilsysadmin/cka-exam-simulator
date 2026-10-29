"""OCI workspace segregation: readonly code, persistent staging state, no symlinks."""
import json
import sys
from pathlib import Path

import pytest

from cka_sim import factory
from cka_sim import __main__ as cli
from cka_sim.catalog import load_bank
from cka_sim.models import Scenario


def proposal():
    data = load_bank()["scale-payment-api"].model_dump(mode="json")
    data.update(slug="staged-capacity", namespace="cka-staged-capacity",
                story="Restore staged service capacity before the seasonal traffic change.")
    return Scenario.model_validate_json(json.dumps(data))


def test_preprovisioned_workspace_dry_run_uses_no_credentials_or_source_writes(
        monkeypatch, tmp_path):
    root = tmp_path / "persistent"
    root.mkdir()
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    seen = []
    result = factory.run_factory(
        workspace=root, generator=lambda **kw: seen.append(kw))
    assert result["status"] == "dry-run" and result["generated"] == []
    assert seen == []
    assert not (root / ".local" / "factory-budget.json").exists()
    assert not (root / "scenarios").exists()
    assert (root / ".local" / "factory.lock").exists()


def test_explicit_staging_generation_writes_only_under_workspace(monkeypatch, tmp_path):
    root = tmp_path / "persistent"
    root.mkdir()
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-only-token")
    result = factory.run_factory(workspace=root, execute=True,
                                 generator=lambda **_: proposal())
    assert result["status"] == "quarantined-not-published"
    assert result["generated"] == ["staged-capacity.yaml"]
    assert (root / "scenarios/generated/staged-capacity.yaml").is_file()
    state = json.loads((root / ".local/factory-budget.json").read_text())
    assert state["attempts"] == 1
    assert not (root / "scenarios/staged-capacity.json").exists()
    assert not (root / "staged-capacity.json").exists()


def test_staging_workspaces_reuse_daily_quota_across_invocations(monkeypatch, tmp_path):
    root = tmp_path / "persistent"
    root.mkdir()
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-only-token")
    requested = []
    def fail(**_):
        requested.append(1)
        raise RuntimeError("provider-error")
    for _ in range(5):
        with pytest.raises(factory.FactoryError, match="attempt counted"):
            factory.run_factory(workspace=root, execute=True, generator=fail)
    with pytest.raises(factory.FactoryError, match="Daily factory"):
        factory.run_factory(workspace=root, execute=True, generator=fail)
    assert len(requested) == 5


@pytest.mark.parametrize("workspace_type", ["missing", "relative", "parent_traversal",
                                           "direct_symlink", "ancestor_symlink",
                                           "intermediate_symlink", "budget_symlink"])
def test_unsafe_workspace_refused_before_network_or_state_changes(
        tmp_path, workspace_type):
    root = tmp_path / "persistent"
    root.mkdir()
    workspace = root
    if workspace_type == "missing":
        workspace = tmp_path / "missing"
    elif workspace_type == "relative":
        workspace = Path("not-absolute")
    elif workspace_type == "parent_traversal":
        workspace = root / ".." / "persistent"
    elif workspace_type == "direct_symlink":
        workspace = tmp_path / "link"
        workspace.symlink_to(root, target_is_directory=True)
    elif workspace_type == "ancestor_symlink":
        parent = tmp_path / "parent-link"
        parent.symlink_to(root, target_is_directory=True)
        workspace = parent / "child"
        (root / "child").mkdir()
    elif workspace_type == "intermediate_symlink":
        (root / "scenarios").symlink_to(tmp_path, target_is_directory=True)
    elif workspace_type == "budget_symlink":
        (root / ".local").symlink_to(tmp_path, target_is_directory=True)
    called = []
    with pytest.raises(factory.FactoryError, match="workspace|Workspace"):
        factory.run_factory(workspace=workspace,
                            generator=lambda **kw: called.append(kw))
    assert not called


def test_workspace_cannot_override_budget_or_quarantine(tmp_path):
    root = tmp_path / "persistent"
    root.mkdir()
    for overrides in ({"directory": tmp_path}, {"state": tmp_path / "state.json"},
                      {"lock": tmp_path / "different.lock"}):
        with pytest.raises(factory.FactoryError, match="combined"):
            factory.run_factory(workspace=root, **overrides)


def test_cli_passes_explicit_workspace_without_affecting_other_commands(
        monkeypatch, tmp_path, capsys):
    root = tmp_path / "staging"
    root.mkdir()
    passed = []
    def fake_factory(**kwargs):
        passed.append(kwargs)
        return {"status": "dry-run"}
    monkeypatch.setattr(cli, "run_factory", fake_factory)
    monkeypatch.setattr(sys, "argv", ["cka-sim", "factory-run", "--workspace", str(root)])
    cli.main()
    assert passed == [{"execute": False, "count": 1,
                       "scenario_type": "deployment_scale", "workspace": root}]
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"
