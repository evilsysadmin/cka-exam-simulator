import json

import httpx
import pytest
from pydantic import ValidationError

from cka_sim.catalog import load_bank, save_generated
from cka_sim.grading import grade_state
from cka_sim.models import Scenario
from cka_sim.ollama import GenerationError, generate
from cka_sim.render import fixture_objects, render_yaml


@pytest.fixture
def bank():
    return load_bank()


def test_bank_contains_two_different_scenarios(bank):
    assert len(bank) == 2
    assert {s.scenario_type for s in bank.values()} == {"deployment_scale", "service_selector"}


def test_fixtures_are_fixed_and_namespaced(bank):
    for s in bank.values():
        docs = fixture_objects(s)
        assert [d["kind"] for d in docs] == ["Namespace", "Deployment", "Service"]
        container = docs[1]["spec"]["template"]["spec"]["containers"][0]
        assert container["image"].startswith("ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine@sha256:")
        assert container["ports"][0]["containerPort"] == 8080
        assert container["securityContext"]["runAsNonRoot"] is True
        assert container["securityContext"]["runAsUser"] == 101
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert docs[2]["spec"]["ports"][0] == {"port": 80, "targetPort": 8080}
        assert docs[2]["metadata"]["namespace"] == s.namespace
        assert "hostPath" not in render_yaml(s)
        assert "privileged" not in render_yaml(s)


@pytest.mark.parametrize("field,value", [
    ("namespace", "default"),
    ("namespace", "cka-../../etc"),
    ("deployment", "foo;rm -rf /"),
    ("slug", "../escape"),
    ("story", "Visit https://invalid.test"),
    ("story", "sudo rm -rf everything"),
])
def test_unsafe_values_rejected(bank, field, value):
    data = bank["scale-payment-api"].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_unknown_executable_field_rejected(bank):
    data = bank["scale-payment-api"].model_dump()
    data["setup_command"] = "cat /etc/passwd"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_initial_states_cannot_pass_by_design(bank):
    for s in bank.values():
        data = s.model_dump()
        if s.scenario_type == "deployment_scale":
            data["initial_replicas"] = s.target_replicas
        else:
            data["initial_selector"] = s.target_selector
        with pytest.raises(ValidationError):
            Scenario.model_validate(data)


def test_candidate_cannot_see_private_fixture(bank):
    view = bank["scale-payment-api"].candidate_view()
    assert "initial_replicas" not in view
    assert "initial_selector" not in view
    assert "target_selector" not in view


def test_generated_proposal_quarantine(bank, tmp_path):
    s = bank["scale-payment-api"]
    output = save_generated(s, tmp_path / "generated")
    assert json.loads(output.read_text())["slug"] == s.slug
    with pytest.raises(FileExistsError):
        save_generated(s, tmp_path / "generated")
    assert not (tmp_path / f"{s.slug}.json").exists()


def test_scale_grading_requires_availability(bank):
    s = bank["scale-payment-api"]
    assert not grade_state(s, deployment={"spec": {"replicas": 1}, "status": {"availableReplicas": 1}}).passed
    assert not grade_state(s, deployment={"spec": {"replicas": 3}, "status": {"availableReplicas": 0}}).passed
    assert grade_state(s, deployment={"spec": {"replicas": 3}, "status": {"availableReplicas": 3}}).passed


def test_selector_grading_requires_correct_selector_and_backends(bank):
    s = bank["fix-catalog-service"]
    dep = {"spec": {"replicas": 2}}
    service = {"spec": {"selector": {"app": s.target_selector}}}
    ready = {"items": [{"endpoints": [{"conditions": {"ready": True}}]}]}
    assert grade_state(s, deployment=dep, service=service, endpoint_slices=ready).passed
    assert not grade_state(s, deployment=dep, service=service, endpoint_slices={"items": []}).passed
    assert not grade_state(s, deployment={}, service=service, endpoint_slices=ready).passed
    assert not grade_state(s, deployment=dep,
                           service={"spec": {"selector": {"app": s.initial_selector}}},
                           endpoint_slices=ready).passed


def test_ollama_receives_json_schema(bank):
    seen = {}
    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"response": bank["scale-payment-api"].model_dump_json()})
    result = generate(model="local-test", transport=httpx.MockTransport(handler))
    assert result.scenario_type == "deployment_scale"
    assert seen["format"]["type"] == "object"
    assert seen["format"]["additionalProperties"] is False
    assert seen["stream"] is False


def test_ollama_rejects_untrusted_script(bank):
    data = bank["scale-payment-api"].model_dump()
    data["grader_code"] = "exit(0)"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"response": json.dumps(data)}))
    with pytest.raises(GenerationError):
        generate(model="test", transport=transport)


def test_ollama_rejects_wrong_scenario_type(bank):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"response": bank["scale-payment-api"].model_dump_json()}))
    with pytest.raises(GenerationError):
        generate(model="test", scenario_type="service_selector", transport=transport)


def test_ollama_network_failure_does_not_publish():
    def fail(request):
        raise httpx.ConnectError("disconnected")
    with pytest.raises(GenerationError):
        generate(model="test", transport=httpx.MockTransport(fail))
