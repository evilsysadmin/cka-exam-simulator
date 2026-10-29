"""Both local and remote LLMs must produce the same trusted scenario DSL."""
import json

import httpx
import pytest
from pydantic import ValidationError

from cka_sim import providers, workers_ai
from cka_sim.catalog import save_generated
from cka_sim.models import Scenario
from cka_sim.ollama import GenerationError
from cka_sim.render import fixture_objects

ACCOUNT = "a" * 32
TOKEN = "test-only-token"


def candidate():
    from cka_sim.catalog import load_bank
    s = load_bank()["scale-payment-api"].model_dump(mode="json")
    s.update(slug="fresh-worker-lab", namespace="cka-fresh-worker")
    return s


@pytest.mark.parametrize("response_body", ["object", "json-string"])
def test_workers_ai_uses_schema_and_fixed_api_host_and_returns_valid_dsl(response_body, tmp_path):
    outgoing = {}
    proposed = candidate()

    def handler(request):
        outgoing["url"] = str(request.url)
        outgoing["auth"] = request.headers.get("Authorization")
        outgoing["body"] = json.loads(request.content)
        answer = proposed if response_body == "object" else json.dumps(proposed)
        return httpx.Response(200, json={"success": True, "result": {"response": answer}})

    scenario = workers_ai.generate(
        account_id=ACCOUNT, api_token=TOKEN,
        model=workers_ai.DEFAULT_MODEL, transport=httpx.MockTransport(handler),
    )
    assert scenario == Scenario.model_validate_json(json.dumps(proposed))
    assert outgoing["url"] == (f"https://api.cloudflare.com/client/v4/accounts/"
                               f"{ACCOUNT}/ai/run/{workers_ai.DEFAULT_MODEL}")
    assert outgoing["auth"] == f"Bearer {TOKEN}"
    assert outgoing["body"]["response_format"]["type"] == "json_schema"
    assert outgoing["body"]["response_format"]["json_schema"]["additionalProperties"] is False
    assert outgoing["body"]["stream"] is False
    assert TOKEN not in json.dumps(outgoing["body"])
    # Remote provider cannot add untrusted image, commands or graders to fixtures.
    assert fixture_objects(scenario)[1]["spec"]["template"]["spec"]["containers"][0]["name"] == "web"
    assert save_generated(scenario, tmp_path / "generated").suffix == ".yaml"


@pytest.mark.parametrize("response", [
    {"success": False, "errors": [{"message": "invalid"}]},
    {"success": True, "result": {"response": None}},
    {"success": True, "result": {"response": {"grader_code": "os.system('id')"}}},
    {"success": True, "result": {"response": {"slug": "incomplete"}}},
])
def test_workers_ai_never_accepts_invalid_responses(response):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    with pytest.raises(GenerationError):
        workers_ai.generate(account_id=ACCOUNT, api_token=TOKEN,
                            transport=transport)


def test_workers_ai_rejects_type_mismatch_and_does_not_echo_secrets():
    proposed = candidate()
    proposed["scenario_type"] = "service_selector"
    proposed["target_replicas"] = proposed["initial_replicas"]
    proposed["initial_selector"] = "old-payment-api"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "success": True, "result": {"response": proposed},
    }))
    with pytest.raises(GenerationError):
        workers_ai.generate(account_id=ACCOUNT, api_token=TOKEN, transport=transport)


@pytest.mark.parametrize("account,token,model", [
    ("not-an-account", TOKEN, workers_ai.DEFAULT_MODEL),
    (ACCOUNT, "", workers_ai.DEFAULT_MODEL),
    (ACCOUNT, TOKEN, "../../ai/run/malicious"),
    (ACCOUNT, TOKEN, "@cf/meta/some-untested-model"),
])
def test_workers_ai_rejects_unsafe_configuration_before_network(account, token, model):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={})
    with pytest.raises(GenerationError):
        workers_ai.generate(account_id=account, api_token=token, model=model,
                            transport=httpx.MockTransport(handler))
    assert requests == []


def test_workers_ai_rejects_http_errors_without_leaking_response_or_token():
    def handler(request):
        return httpx.Response(403, json={"errors": [{"message": TOKEN}]})
    with pytest.raises(GenerationError) as exc:
        workers_ai.generate(account_id=ACCOUNT, api_token=TOKEN,
                            transport=httpx.MockTransport(handler))
    assert TOKEN not in str(exc.value)


def test_provider_routing_and_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.ollama, "generate",
                        lambda **kwargs: calls.append(("local", kwargs)) or "local")
    monkeypatch.setattr(providers.workers_ai, "generate",
                        lambda **kwargs: calls.append(("remote", kwargs)) or "remote")
    assert providers.default_model("workers-ai") == workers_ai.DEFAULT_MODEL
    assert providers.default_model("ollama").startswith("qwen2.5:")
    assert providers.generate(provider="workers-ai", model=workers_ai.DEFAULT_MODEL) == "remote"
    assert providers.generate(provider="ollama", model="tiny") == "local"
    assert calls[0][0] == "remote" and calls[1][0] == "local"
    with pytest.raises(ValueError):
        providers.generate(provider="invalid", model="unknown")


def test_workers_ai_does_not_enable_new_scenario_types():
    with pytest.raises(ValueError, match="Unsupported"):
        workers_ai.generate(account_id=ACCOUNT, api_token=TOKEN,
                            scenario_type="deployment_resources")
