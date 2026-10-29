from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cka_sim import kube
from cka_sim.api import app, dev_only

client = TestClient(app)


def test_health_and_scenarios_are_readable():
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert "initial_replicas" not in response.json()[0]


def test_unknown_scenario_404():
    assert client.get("/api/scenarios/unknown").status_code == 404


def test_lab_mutations_disabled_without_flag(monkeypatch):
    monkeypatch.delenv("CKA_ENABLE_LOCAL_LAB", raising=False)
    headers = {"X-CKA-Lab-Request": "1"}
    assert client.post("/api/dev/scenarios/scale-payment-api/setup", headers=headers).status_code == 403
    assert client.post("/api/dev/scenarios/scale-payment-api/grade", headers=headers).status_code == 403
    assert client.post("/api/dev/terminal/command", json={"command": "kubectl get pods"}, headers=headers).status_code == 403


def test_lab_mutations_require_header_and_loopback(monkeypatch):
    monkeypatch.setenv("CKA_ENABLE_LOCAL_LAB", "1")
    assert client.post("/api/dev/scenarios/scale-payment-api/setup").status_code == 403
    for request in [
        Mock(client=Mock(host="203.0.113.5"), headers={"X-CKA-Lab-Request": "1"}),
        Mock(client=Mock(host="127.0.0.1"), headers={}),
    ]:
        with pytest.raises(HTTPException) as exc:
            dev_only(request)
        assert exc.value.status_code == 403


@pytest.mark.parametrize("command", [
    "sh -c 'kubectl get pods'", "kubectl exec pod -- sh",
    "kubectl delete ns prod", "kubectl get pods; rm -rf /",
    "kubectl get pods | cat", "kubectl get --raw /api",
    "kubectl get pods --kubeconfig=/tmp/prod",
    "kubectl get pods --context production",
    "kubectl get pods -f /etc/passwd",
    "kubectl get pods\nkubectl delete nodes",
    "kubectl get pods --server=https://cluster",
    "kubectl get pods --", "kubectl get pods --patch-file /tmp/patch",
])
def test_terminal_rejects_dangerous_commands(command):
    with pytest.raises(kube.LabError):
        kube.execute_dev_kubectl(command)


def test_terminal_uses_dedicated_kubeconfig(monkeypatch, tmp_path):
    config = tmp_path / "kubeconfig"
    config.write_text("fake")
    monkeypatch.setattr(kube, "KUBECONFIG", config)
    calls = {}
    def fake_run(binary, args, **kwargs):
        calls.update({"binary": binary, "args": args})
        return "ok"
    monkeypatch.setattr(kube, "_run", fake_run)
    assert kube.execute_dev_kubectl("kubectl get pods -n cka-payments") == "ok"
    assert calls["binary"] == "kubectl"
    assert calls["args"][:4] == ["--kubeconfig", str(config), "--context", "kind-cka-dev"]


def test_lab_delete_cannot_target_another_cluster(monkeypatch):
    calls = {}
    monkeypatch.setattr(kube, "_run", lambda binary, args, **kwargs: calls.update({"binary": binary, "args": args}) or "")
    kube.delete_lab()
    assert calls == {"binary": "kind", "args": ["delete", "cluster", "--name", "cka-dev"]}


def test_missing_deployment_is_a_failed_solution_not_outage(monkeypatch):
    from cka_sim.catalog import load_bank
    s = load_bank()["scale-payment-api"]
    def missing(*args, **kwargs):
        raise kube.LabError('Error from server (NotFound): deployments.apps "payment-api" not found')
    monkeypatch.setattr(kube, "kubectl", missing)
    assert kube.grade(s)["passed"] is False
