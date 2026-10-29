"""CI must discover all curated scenarios, not just a hardcoded shortlist."""
import sys

import pytest

from cka_sim import __main__ as cli


def test_lab_smoke_all_visits_every_curated_scenario(monkeypatch, capsys):
    scenarios = {"scenario-a": object(), "scenario-b": object(), "newly-added": object()}
    seen = []
    monkeypatch.setattr(cli, "load_bank", lambda: scenarios)
    monkeypatch.setattr(cli, "_smoke_scenario", lambda s: seen.append(s))
    monkeypatch.setattr(sys, "argv", ["cka-sim", "lab-smoke-all"])
    cli.main()
    assert seen == list(scenarios.values())
    assert "all 3 curated" in capsys.readouterr().out


def test_lab_smoke_all_fails_closed_for_empty_catalog(monkeypatch):
    monkeypatch.setattr(cli, "load_bank", lambda: {})
    monkeypatch.setattr(sys, "argv", ["cka-sim", "lab-smoke-all"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_lab_smoke_all_stops_on_first_failure(monkeypatch):
    scenarios = {"first": object(), "second": object(), "third": object()}
    visited = []
    monkeypatch.setattr(cli, "load_bank", lambda: scenarios)
    def failing_smoke(item):
        visited.append(item)
        raise cli.LabError("failed Kubernetes smoke")
    monkeypatch.setattr(cli, "_smoke_scenario", failing_smoke)
    monkeypatch.setattr(sys, "argv", ["cka-sim", "lab-smoke-all"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert visited == [scenarios["first"]]
