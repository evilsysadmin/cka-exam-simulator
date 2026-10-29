"""Contract tests for the one-process, loopback-only exam clock."""
from fastapi.testclient import TestClient
import pytest

from cka_sim import api, session
from cka_sim.kube import LabError

client = TestClient(api.app)
HEADERS = {"X-CKA-Lab-Request": "1"}
SCALE = "scale-payment-api"
SERVICE = "fix-catalog-service"


@pytest.fixture
def local_clock(monkeypatch):
    seconds = [1000.0]
    monkeypatch.setenv("CKA_ENABLE_LOCAL_LAB", "1")
    monkeypatch.setattr(session, "_session", None)
    monkeypatch.setattr(session, "monotonic", lambda: seconds[0])
    monkeypatch.setattr(api, "setup", lambda _: None)
    monkeypatch.setattr(api, "execute_dev_kubectl", lambda _: "pods listed")
    monkeypatch.setattr(api, "grade", lambda _: {"passed": True, "score": 100, "checks": []})
    yield seconds
    monkeypatch.setattr(session, "_session", None)


def test_no_session_is_safe_and_mutations_are_blocked(local_clock):
    assert client.get("/api/dev/session", headers=HEADERS).json() == {"session": None}
    assert client.post(f"/api/dev/scenarios/{SCALE}/grade", headers=HEADERS).status_code == 409
    assert client.post("/api/dev/terminal/command", headers=HEADERS,
                       json={"command": "kubectl get pods"}).status_code == 409
    assert client.get("/api/dev/session").status_code == 403


def test_clock_survives_browser_refresh_and_locks_terminal_at_expiry(local_clock):
    response = client.post(f"/api/dev/scenarios/{SCALE}/setup", headers=HEADERS)
    assert response.status_code == 200
    original = response.json()["session"]
    assert original["scenario_slug"] == SCALE
    assert original["remaining_seconds"] == 360
    assert original["phase"] == "active"
    assert original["expires_at"].endswith("+00:00")
    local_clock[0] += 70
    restored = client.get("/api/dev/session", headers=HEADERS).json()["session"]
    assert restored["remaining_seconds"] == 290
    assert restored["expires_at"] == original["expires_at"]
    command = {"command": "kubectl get pods"}
    assert client.post("/api/dev/terminal/command", headers=HEADERS, json=command).status_code == 200
    local_clock[0] += 290
    assert client.get("/api/dev/session", headers=HEADERS).json()["session"]["phase"] == "expired"
    assert client.post("/api/dev/terminal/command", headers=HEADERS, json=command).status_code == 409
    assert client.post(f"/api/dev/scenarios/{SCALE}/grade", headers=HEADERS).json()["score"] == 100


def test_switching_scenarios_cannot_grade_a_different_session(local_clock):
    assert client.post(f"/api/dev/scenarios/{SCALE}/setup", headers=HEADERS).status_code == 200
    assert client.post(f"/api/dev/scenarios/{SERVICE}/grade", headers=HEADERS).status_code == 409
    response = client.post(f"/api/dev/scenarios/{SERVICE}/setup", headers=HEADERS)
    assert response.json()["session"]["scenario_slug"] == SERVICE
    assert client.post(f"/api/dev/scenarios/{SCALE}/grade", headers=HEADERS).status_code == 409


def test_provisioning_failure_never_starts_clock(local_clock, monkeypatch):
    def fail(_):
        raise LabError("fixture failed")
    monkeypatch.setattr(api, "setup", fail)
    assert client.post(f"/api/dev/scenarios/{SCALE}/setup", headers=HEADERS).status_code == 503
    assert client.get("/api/dev/session", headers=HEADERS).json() == {"session": None}


def test_late_setup_starts_fresh_time(local_clock):
    client.post(f"/api/dev/scenarios/{SCALE}/setup", headers=HEADERS)
    local_clock[0] += 400
    response = client.post(f"/api/dev/scenarios/{SCALE}/setup", headers=HEADERS)
    assert response.json()["session"]["remaining_seconds"] == 360
    assert response.json()["session"]["phase"] == "active"
