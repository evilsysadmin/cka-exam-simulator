"""Real signed tokens/HTTP freshness around the serialized lab actor boundary."""
from dataclasses import asdict
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from cka_sim.catalog import load_bank
from cka_sim.lab_api import HOST, ORIGIN, create_lab_app, pilot_authorizer
from cka_sim.lab_controller import LabController
from cka_sim.lease_process import LeaseRejected
from cka_sim.leased_kube import ClusterUnavailable
from test_identity_freshness import material, signed, SUBJECT


class Journal:
    def __init__(self):
        self.lease = None
        self.phase = "active"
        self.remaining = 300
        self.confirmed = 0

    def start(self, owner, slug):
        if self.lease:
            raise LeaseRejected("capacity")
        scenario = load_bank()[slug].model_copy(update={"namespace": "cka-lab-" + "b" * 32})
        self.lease = {"ID": "a" * 32, "Namespace": scenario.namespace,
                      "Scenario": scenario.model_dump(mode="json")}
        self.owner = owner
        self.phase = "active"
        return self.lease

    def status(self, owner, identity):
        if not self.lease or identity != self.lease["ID"] or owner != self.owner:
            raise LeaseRejected("denied")
        return {"id": identity, "scenario_slug": self.lease["Scenario"]["slug"],
                "phase": self.phase, "remaining_seconds": self.remaining,
                "expires_at": "2099-01-01T00:00:00Z"}

    def scenario(self, owner, identity):
        self.status(owner, identity)
        return self.lease["Scenario"]

    def finish(self, owner, identity):
        self.status(owner, identity)
        self.phase = "closed"
        return {"ID": identity, "Namespace": self.lease["Namespace"]}

    def pending(self):
        return [{"ID": self.lease["ID"], "Namespace": self.lease["Namespace"]}] if self.lease and self.phase == "closed" else []

    def confirm_cleanup(self, identity, namespace):
        assert self.phase == "closed" and identity == self.lease["ID"] and namespace == self.lease["Namespace"]
        self.confirmed += 1
        self.lease = None


class Cluster:
    def __init__(self):
        self.calls = []
        self.broken = False
        self.prepare_broken = False

    def ready(self):
        return True

    def provision(self, identity, scenario):
        self.calls.append("provision")
        if self.prepare_broken:
            raise ClusterUnavailable()

    def command(self, identity, scenario, command):
        self.calls.append("command")
        return "candidate-scoped result"

    def grade(self, identity, scenario):
        self.calls.append("grade")
        return {"passed": False, "score": 0, "checks": []}

    def cleanup(self, identity, namespace):
        self.calls.append("cleanup")
        if self.broken:
            raise ClusterUnavailable()


def setup(material):
    state = {"revoked": False, "calls": 0}
    def authority(request):
        state["calls"] += 1
        assert str(request.url) == "https://id-staging.shadowops.dpdns.org/api/identity/freshness"
        assert "origin" not in request.headers and "cookie" not in request.headers
        return httpx.Response(401) if state["revoked"] else httpx.Response(200, json={
            **asdict(material[3]), "audience": "cka-simulator"})
    journal, cluster = Journal(), Cluster()
    controller = LabController(leases=journal, cluster=cluster, bank=load_bank(),
        authenticate=pilot_authorizer(jwks=material[1], credential="soc1_" + "A" * 43,
            pilot_subject=SUBJECT, transport=httpx.MockTransport(authority)), pilot_subject=SUBJECT)
    api = TestClient(create_lab_app(controller=controller, build="a" * 40), base_url="https://" + HOST)
    headers = {"Origin": ORIGIN, "Authorization": "Bearer " + signed(material[0], material[2])}
    return api, headers, controller, journal, cluster, state


def post(api, headers, action, **data):
    return api.post("/api/labs/" + action, headers=headers, json=data)


def test_each_candidate_operation_checks_actual_signature_and_freshness(material):
    api, headers, controller, journal, cluster, state = setup(material)
    assert post(api, headers, "capabilities").status_code == 200
    prepared = post(api, headers, "prepare", slug="scale-payment-api")
    assert prepared.status_code == 200
    assert set(prepared.json()) == {"session", "scenario"}
    assert "initial_replicas" not in json.dumps(prepared.json())
    identity = prepared.json()["session"]["id"]
    for action, data in [("status", {}), ("command", {"command": "kubectl get pods"}), ("grade", {}), ("finish", {})]:
        assert post(api, headers, action, id=identity, **data).status_code == 200
    assert state["calls"] == 6
    assert cluster.calls == ["provision", "command", "grade", "cleanup"]
    assert journal.confirmed == 1
    assert post(api, headers, "command", id=identity, command="kubectl get pods").status_code == 401
    assert cluster.calls[-1] == "cleanup"


def test_logout_revocation_blocks_commands_status_grade_and_cleanup_before_token_expiry(material):
    api, headers, controller, journal, cluster, state = setup(material)
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 200
    state["revoked"] = True
    for action in ["status", "command", "grade", "finish"]:
        data = {"id": "a" * 32}
        if action == "command":
            data["command"] = "kubectl get pods"
        assert post(api, headers, action, **data).status_code == 401
    assert cluster.calls == ["provision"] and journal.phase == "active"


def test_forged_wrong_audience_and_unallowlisted_identity_never_touch_leases(material):
    api, headers, controller, journal, cluster, state = setup(material)
    for claims in [{**material[2], "aud": "chronicles"},
                   {**material[2], "sub": "22222222-2222-4222-8222-222222222222"}]:
        altered = {**headers, "Authorization": "Bearer " + signed(material[0], claims)}
        assert post(api, altered, "prepare", slug="scale-payment-api").status_code == 401
    assert post(api, {**headers, "Authorization": headers["Authorization"] + "bad"}, "capabilities").status_code == 401
    assert state["calls"] == 0 and not cluster.calls and journal.lease is None


def test_expiring_foreign_and_concurrent_requests_cannot_execute(material):
    api, headers, controller, journal, cluster, state = setup(material)
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 200
    assert post(api, headers, "command", id="c" * 32, command="kubectl get pods").status_code == 401
    journal.remaining = 12
    assert post(api, headers, "command", id="a" * 32, command="kubectl get pods").status_code == 401
    controller.lock.acquire()
    try:
        count = state["calls"]
        assert post(api, headers, "grade", id="a" * 32).status_code == 409
        assert state["calls"] == count
    finally:
        controller.lock.release()
    assert cluster.calls == ["provision"]


def test_failed_cleanup_keeps_the_slot_until_private_reconciliation_succeeds(material):
    api, headers, controller, journal, cluster, state = setup(material)
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 200
    cluster.broken = True
    assert post(api, headers, "finish", id="a" * 32).status_code == 503
    assert journal.confirmed == 0 and not controller.reap()
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 409
    cluster.broken = False
    assert controller.reap() and journal.confirmed == 1
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 200


def test_partial_preparation_is_closed_and_retained_for_verified_cleanup(material):
    api, headers, controller, journal, cluster, state = setup(material)
    cluster.prepare_broken = True
    assert post(api, headers, "prepare", slug="scale-payment-api").status_code == 503
    assert journal.phase == "closed" and journal.confirmed == 0
    assert controller.reap() and journal.confirmed == 1


def test_browser_and_strict_body_boundaries_deny_before_authority(material):
    api, headers, controller, journal, cluster, state = setup(material)
    for changes in [{"Origin": "https://other.invalid"}, {"Cookie": "session=bad"}, {"Host": "other.invalid"}]:
        assert post(api, {**headers, **changes}, "capabilities").status_code == 403
    assert api.post("/api/labs/capabilities?token=bad", headers=headers, json={}).status_code == 403
    assert post(api, headers, "prepare", slug="scale-payment-api", owner=SUBJECT).status_code == 400
    assert api.post("/api/labs/prepare", headers={**headers, "Content-Type": "application/json"},
                    content='{"slug":"scale-payment-api","slug":"different"}').status_code == 400
    assert api.post("/api/labs/command", headers={**headers, "Content-Type": "application/json"},
                    content="x" * 2049).status_code == 413
    assert api.post("/api/labs/capabilities", headers=[("Origin", ORIGIN), ("Content-Type", "application/json"),
                    ("Authorization", "Bearer a"), ("Authorization", "Bearer b")], content="{}").status_code == 401
    assert state["calls"] == 0 and not cluster.calls
    response = api.options("/api/labs/prepare", headers={"Origin": ORIGIN,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["cache-control"] == "no-store"
    assert api.get("/api/dev/session").status_code == 404


def test_config_requires_verified_uuid_instead_of_username(material):
    with pytest.raises(ValueError):
        LabController(leases=Journal(), cluster=Cluster(), bank=load_bank(),
                      authenticate=lambda _: SUBJECT, pilot_subject="evilsysadmin")


def test_owner_recovers_an_attempt_after_reload_or_a_lost_prepare_response(material):
    api, headers, controller, journal, cluster, state = setup(material)
    assert post(api, headers, "capabilities").json()["current"] is None
    prepared = post(api, headers, "prepare", slug="scale-payment-api").json()
    current = post(api, headers, "capabilities").json()["current"]
    assert current == prepared
    state["revoked"] = True
    assert post(api, headers, "capabilities").status_code == 401
    state["revoked"] = False
    assert post(api, headers, "finish", id=current["session"]["id"]).status_code == 200
    assert post(api, headers, "capabilities").json()["current"] is None
