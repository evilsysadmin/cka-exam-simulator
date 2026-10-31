"""Serialized private actor: fresh Identity -> owned lease -> real cluster.

The authorizer must be the trusted signature AND authoritative freshness check.
It runs under the actor lock on every candidate operation, including status and
finish. A request/body UUID is never accepted. Only the operator binds the pilot.
"""
from __future__ import annotations

import json
import re
import threading

from .candidate_kubectl import CandidateCommandError
from .lease_process import LeaseRejected, LeaseUnavailable
from .leased_kube import ClusterUnavailable


UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}")
ID = re.compile(r"[a-f0-9]{32}")


class LabDenied(RuntimeError):
    pass


class LabBusy(RuntimeError):
    pass


class LabUnavailable(RuntimeError):
    pass


def scenario_model(data):
    from .models import Scenario
    return Scenario.model_validate_json(json.dumps(data))


class LabController:
    def __init__(self, *, leases, cluster, bank, authenticate, pilot_subject,
                 parse_scenario=scenario_model):
        if (type(pilot_subject) is not str or not UUID.fullmatch(pilot_subject)
                or not callable(authenticate) or not bank):
            raise ValueError("Verified pilot UUID and curated bank required")
        self.leases, self.cluster, self.bank = leases, cluster, bank
        self.authenticate, self.pilot_subject = authenticate, pilot_subject
        self.parse_scenario = parse_scenario
        self.lock = threading.Lock()

    def _owner(self, token):
        try:
            subject = self.authenticate(token)
        except Exception:
            raise LabDenied("Authentication required") from None
        if type(subject) is not str or subject != self.pilot_subject:
            raise LabDenied("Lab access unavailable")
        return subject

    def _active(self, owner, identity):
        if type(identity) is not str or not ID.fullmatch(identity):
            raise LabDenied("Session unavailable")
        view = self.leases.status(owner, identity)
        # Every launched child can finish within this remaining TTL budget.
        if view.get("phase") != "active" or type(view.get("remaining_seconds")) is not int or view["remaining_seconds"] <= 12:
            raise LabDenied("Session expired or ending")
        scenario = self.parse_scenario(self.leases.scenario(owner, identity))
        if scenario.slug != view.get("scenario_slug") or scenario.slug not in self.bank:
            raise LabUnavailable("Lab state unavailable")
        return view, scenario

    def operation(self, token, action, *, identity=None, slug=None, command=None):
        if not self.lock.acquire(blocking=False):
            raise LabBusy("Lab operation in progress")
        try:
            owner = self._owner(token)
            if action == "capabilities":
                return {"labs_enabled": True, "capacity": 1,
                        "scenarios": [s.candidate_view() for s in self.bank.values()]}
            if action == "prepare":
                if type(slug) is not str or slug not in self.bank:
                    raise LabDenied("Scenario unavailable")
                lease = self.leases.start(owner, slug)
                identity = lease["ID"]
                try:
                    scenario = self.parse_scenario(lease["Scenario"])
                    if scenario.slug != slug or scenario.namespace != lease["Namespace"]:
                        raise ValueError()
                    self.cluster.provision(identity, scenario)
                    view, _ = self._active(owner, identity)
                    return {"session": view, "scenario": scenario.candidate_view()}
                except Exception:
                    # Retain the slot and queue reconciliation even on partial setup.
                    self.leases.finish(owner, identity)
                    raise LabUnavailable("Lab preparation unavailable") from None
            if action == "status":
                view, scenario = self._active(owner, identity)
                return {"session": view, "scenario": scenario.candidate_view()}
            if action == "finish":
                if type(identity) is not str or not ID.fullmatch(identity):
                    raise LabDenied("Session unavailable")
                cleanup = self.leases.finish(owner, identity)
                self._cleanup(cleanup)
                return {"status": "closed"}
            if action in {"command", "grade"}:
                _, scenario = self._active(owner, identity)
                if action == "command":
                    return {"output": self.cluster.command(identity, scenario, command)}
                return self.cluster.grade(identity, scenario)
            raise LabDenied("Operation unavailable")
        except LeaseRejected as exc:
            if exc.code in {"capacity", "conflict"}:
                raise LabBusy("Lab occupied; finish the active attempt or wait for cleanup") from None
            raise LabDenied("Session unavailable") from None
        except CandidateCommandError:
            raise
        except (LeaseUnavailable, ClusterUnavailable, ValueError, KeyError, TypeError):
            raise LabUnavailable("Lab temporarily unavailable") from None
        finally:
            self.lock.release()

    def _cleanup(self, cleanup):
        # The cluster driver checks namespace labels and UID, waits for deletion,
        # and independently verifies absence. Never confirm from an HTTP request.
        self.cluster.cleanup(cleanup["ID"], cleanup["Namespace"])
        self.leases.confirm_cleanup(cleanup["ID"], cleanup["Namespace"])

    def reap(self):
        """Private lifespan worker only. Restart/expiry never bypass deletion."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if not self.cluster.ready():
                return False
            for cleanup in self.leases.pending():
                self._cleanup(cleanup)
            return True
        except Exception:
            return False  # leave the durable slot occupied; never restart journal
        finally:
            self.lock.release()
