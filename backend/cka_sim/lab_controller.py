"""Serialized private actor: fresh Identity -> owned lease -> real cluster.

The authorizer must be the trusted signature AND authoritative freshness check.
It runs under the actor lock on every candidate operation, including status and
finish. A request/body UUID is never accepted. The operator explicitly selects
the restricted pilot or authenticated staging POC; capacity stays globally one.
"""
from __future__ import annotations

import json
import re
import threading

from .candidate_kubectl import CandidateCommandError
from .lab_editor import EditRejected, edit_target, edit_patch
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


class LabRateLimited(RuntimeError):
    def __init__(self):
        super().__init__("Lab request budget exhausted")


def scenario_model(data):
    from .models import Scenario
    return Scenario.model_validate_json(json.dumps(data))


class LabController:
    def __init__(self, *, leases, cluster, bank, authenticate, pilot_subject,
                 access_mode="pilot", parse_scenario=scenario_model, editors=None, authenticate_editor=None):
        if (access_mode not in {"pilot", "authenticated-staging"}
                or not callable(authenticate) or not bank
                or (access_mode == "pilot" and (type(pilot_subject) is not str or not UUID.fullmatch(pilot_subject)))
                or (access_mode == "authenticated-staging" and pilot_subject is not None)):
            raise ValueError("Explicit staging access policy and curated bank required")
        self.leases, self.cluster, self.bank = leases, cluster, bank
        self.authenticate, self.pilot_subject = authenticate, pilot_subject
        self.access_mode = access_mode
        self.parse_scenario = parse_scenario
        self.lock = threading.Lock()
        # Recovery pointer only. Go still checks the owner/phase on every read;
        # a controller restart closes/reaps its durable leases before reuse.
        self.current = None
        self.editors = editors
        self.authenticate_editor = authenticate_editor

    def _owner(self, token, *, editing=False):
        try:
            authorizer = self.authenticate_editor if editing and self.authenticate_editor else self.authenticate
            subject = authorizer(token)
        except LabRateLimited:
            raise
        except Exception:
            raise LabDenied("Authentication required") from None
        if (type(subject) is not str or not UUID.fullmatch(subject)
                or (self.access_mode == "pilot" and subject != self.pilot_subject)):
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

    def operation(self, token, action, *, identity=None, slug=None, command=None, editor=None):
        if not self.lock.acquire(blocking=False):
            raise LabBusy("Lab operation in progress")
        try:
            if action == 'edit-check':
                # An opaque owned editor ticket selects the heartbeat budget,
                # never authority. Signature/freshness and lease owner follow.
                current = self.editors.current if self.editors else None
                if not current or current.identity != editor or current.lease != identity:
                    raise LabDenied('Editor unavailable')
            owner = self._owner(token,editing=action == 'edit-check')
            if action == "capabilities":
                current = None
                if self.current and self.current[0] == owner:
                    _, identity, scenario = self.current
                    try:
                        view = self.leases.status(owner, identity)
                        current = {"session": view, "scenario": scenario.candidate_view()}
                    except LeaseRejected:
                        self.current = None
                return {"labs_enabled": True, "capacity": 1,
                        "available": self.current is None,
                        "scenarios": [s.candidate_view() for s in self.bank.values()], "current": current}
            if action == "prepare":
                if type(slug) is not str or slug not in self.bank:
                    raise LabDenied("Scenario unavailable")
                lease = self.leases.start(owner, slug)
                identity = lease["ID"]
                try:
                    scenario = self.parse_scenario(lease["Scenario"])
                    if scenario.slug != slug or scenario.namespace != lease["Namespace"]:
                        raise ValueError()
                    self.current = (owner, identity, scenario)
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
            if action in {'edit-open','edit-check','edit-save','edit-cancel'}:
                view, scenario = self._active(owner, identity)
                if not self.editors:
                    raise EditRejected('unavailable')
                if action == 'edit-open':
                    kind, name = edit_target(command, scenario)
                    snapshot = self.cluster.edit_snapshot(identity, scenario, kind, name)
                    return self.editors.open(owner,identity,scenario,command,snapshot,view['remaining_seconds'])
                state = self.editors.owned(owner,identity,editor)
                if action == 'edit-check':
                    return state
                if action == 'edit-cancel':
                    self.editors.close()
                    return {'status':'closed'}
                try:
                    patch = edit_patch(state.original,self.editors.saved(state))
                    if patch:
                        self.cluster.edit_apply(identity,scenario,state.kind,state.name,patch)
                    return {'output':'Cambios guardados en Kubernetes.' if patch else 'Sin cambios en Kubernetes.'}
                finally:
                    self.editors.close()
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
        except (CandidateCommandError, EditRejected):
            raise
        except (LeaseUnavailable, ClusterUnavailable, ValueError, KeyError, TypeError):
            raise LabUnavailable("Lab temporarily unavailable") from None
        finally:
            self.lock.release()

    def _cleanup(self, cleanup):
        # The cluster driver checks namespace labels and UID, waits for deletion,
        # and independently verifies absence. Never confirm from an HTTP request.
        if self.editors:
            self.editors.close_lease(cleanup["ID"])
        self.cluster.cleanup(cleanup["ID"], cleanup["Namespace"])
        self.leases.confirm_cleanup(cleanup["ID"], cleanup["Namespace"])
        if self.current and self.current[1] == cleanup["ID"]:
            self.current = None

    def reap(self):
        """Private lifespan worker only. Restart/expiry never bypass deletion."""
        if not self.lock.acquire(blocking=False):
            # A candidate operation legitimately holds this lock while provisioning,
            # grading or cleaning up. No health check ran: keep the last verdict.
            return None
        try:
            if self.editors:
                self.editors.reap()
            if not self.cluster.ready():
                return False
            for cleanup in self.leases.pending():
                self._cleanup(cleanup)
            return True
        except Exception:
            return False  # leave the durable slot occupied; never restart journal
        finally:
            self.lock.release()
