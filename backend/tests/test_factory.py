"""Conservative OCI Workers AI factory: no inference until explicitly enabled."""
import fcntl
import json

import pytest

from cka_sim import factory
from cka_sim.catalog import load_bank
from cka_sim.models import Scenario


ACCOUNT = "a" * 32
TOKEN = "do-not-expose-test-token"


def options(tmp_path):
    home = tmp_path / ".local"
    return {"directory": tmp_path / "generated",
            "state": home / "factory-budget.json",
            "lock": home / "factory.lock"}


def candidate(slug="factory-one", namespace="cka-factory-one"):
    raw = load_bank()["scale-payment-api"].model_dump(mode="json")
    raw.update(slug=slug, namespace=namespace,
               story=f"Repair the replica count for {slug} after a scheduled service migration.")
    return Scenario.model_validate_json(json.dumps(raw))


def test_dry_run_never_contacts_workers_ai_or_requires_secrets(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    called = []
    result = factory.run_factory(generator=lambda **kw: called.append(kw), **args)
    assert result["status"] == "dry-run" and result["generated"] == []
    assert result["provider"] == "workers-ai" and result["planned"] == 1
    assert called == []
    assert not args["state"].exists()


def test_explicit_single_run_quarantines_but_never_publishes(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    result = factory.run_factory(
        execute=True, generator=lambda **_: candidate(), **args)
    assert result["status"] == "quarantined-not-published"
    assert result["generated"] == ["factory-one.yaml"]
    assert result["daily_remaining_after"] == 4
    assert (args["directory"] / "factory-one.yaml").exists()
    assert not (args["directory"].parent / "scenarios" / "factory-one.json").exists()
    state = json.loads(args["state"].read_text())
    assert state["attempts"] == 1
    assert TOKEN not in json.dumps(result) + args["state"].read_text()


def test_model_failure_counts_budget_and_never_leaks_provider_response(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    def fail(**kwargs):
        raise RuntimeError("secret=" + TOKEN)
    with pytest.raises(factory.FactoryError, match="attempt counted") as exc:
        factory.run_factory(execute=True, generator=fail, **args)
    assert TOKEN not in str(exc.value)
    assert json.loads(args["state"].read_text())["attempts"] == 1


def test_daily_budget_caps_remote_attempts(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    requests = []
    def fail(**_):
        requests.append(1)
        raise RuntimeError("Transient Cloudflare failure")
    for _ in range(4):
        with pytest.raises(factory.FactoryError, match="attempt counted"):
            factory.run_factory(execute=True, generator=fail, **args)
    assert len(requests) == 4
    with pytest.raises(factory.FactoryError, match="Daily factory"):
        factory.run_factory(execute=True, count=2, generator=fail, **args)
    success = factory.run_factory(execute=True, generator=lambda **_: candidate(), **args)
    assert success["daily_remaining_after"] == 0
    with pytest.raises(factory.FactoryError, match="Daily factory"):
        factory.run_factory(execute=True, generator=fail, **args)
    assert len(requests) == 4


def test_quarantine_limit_without_any_remote_inference(tmp_path):
    args = options(tmp_path)
    from cka_sim.catalog import save_generated
    for n in range(5):
        save_generated(candidate(f"factory-{n}", f"cka-factory-{n}"), args["directory"])
    with pytest.raises(factory.FactoryError, match="quarantine limit"):
        factory.run_factory(**args)

@pytest.mark.parametrize("count", [0, 3, -1, True, 1.5])
def test_unbounded_requests_rejected_before_any_network(tmp_path, count):
    with pytest.raises(factory.FactoryError, match="count"):
        factory.run_factory(count=count, **options(tmp_path))


def test_invalid_or_mismatched_workers_config_fails_before_inference(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(factory.FactoryError, match="token"):
        factory.run_factory(execute=True, **args)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CKA_SCENARIO_PROVIDER", "ollama")
    with pytest.raises(factory.FactoryError, match="never Ollama"):
        factory.run_factory(execute=True, **args)
    monkeypatch.setenv("CKA_SCENARIO_PROVIDER", "workers-ai")
    with pytest.raises(factory.FactoryError, match="allowlist"):
        factory.run_factory(model="anything/unsafe", execute=True, **args)


def test_invalid_quarantine_and_corrupt_state_fail_closed(tmp_path):
    args = options(tmp_path)
    args["directory"].mkdir()
    (args["directory"] / "broken.yaml").write_text("broken: [")
    with pytest.raises(factory.FactoryError, match="offline review"):
        factory.run_factory(**args)
    (args["directory"] / "broken.yaml").unlink()
    args["state"].write_text("not-json")
    with pytest.raises(factory.FactoryError, match="budget file"):
        factory.run_factory(**args)


def test_future_budget_date_fails_closed(tmp_path):
    args = options(tmp_path)
    args["lock"].parent.mkdir()
    args["state"].write_text(json.dumps({"utc_date": "2999-01-01", "attempts": 1}))
    with pytest.raises(factory.FactoryError, match="budget"):
        factory.run_factory(**args)


def test_symlink_quarantine_and_state_never_followed(tmp_path):
    args = options(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    args["directory"].symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(factory.FactoryError, match="quarantine"):
        factory.run_factory(**args)
    args["directory"].unlink()
    args["state"].symlink_to(tmp_path / "missing")
    with pytest.raises(factory.FactoryError, match="budget"):
        factory.run_factory(**args)


def test_concurrent_factory_lock_refuses_second_process(tmp_path):
    args = options(tmp_path)
    args["lock"].parent.mkdir()
    with args["lock"].open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(factory.FactoryError, match="in progress"):
            factory.run_factory(**args)


def test_no_generations_for_missing_credentials_even_with_budget(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    called = []
    with pytest.raises(factory.FactoryError):
        factory.run_factory(execute=True, generator=lambda **_: called.append(1), **args)
    assert called == []
    assert not args["state"].exists()
