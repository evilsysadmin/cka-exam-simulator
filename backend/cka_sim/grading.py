"""Deterministic, read-only checks of Kubernetes resource snapshots."""
from dataclasses import dataclass
from typing import Any
from .models import Scenario


@dataclass(frozen=True)
class Grade:
    passed: bool
    checks: tuple[dict, ...]

    def as_dict(self):
        return {"passed": self.passed, "checks": list(self.checks),
                "score": 100 if self.passed else 0}


def grade_state(s: Scenario, *, deployment: dict[str, Any],
                service: dict[str, Any] | None = None,
                endpoint_slices: dict[str, Any] | None = None) -> Grade:
    checks = []
    if s.scenario_type == "deployment_scale":
        desired = deployment.get("spec", {}).get("replicas")
        available = deployment.get("status", {}).get("availableReplicas", 0)
        checks.append({"name": "Réplicas declaradas", "passed": desired == s.target_replicas})
        checks.append({"name": "Réplicas disponibles", "passed": available >= s.target_replicas})
    else:
        selector = (service or {}).get("spec", {}).get("selector")
        slices = (endpoint_slices or {}).get("items") or []
        ready = sum(1 for item in slices for ep in (item.get("endpoints") or [])
                    if ep.get("conditions", {}).get("ready") is True)
        checks.append({"name": "Selector del Service", "passed": selector == {"app": s.target_selector}})
        checks.append({"name": "Backends listos", "passed": ready >= 1})
        checks.append({"name": "Deployment conservado", "passed":
                      deployment.get("spec", {}).get("replicas") == s.target_replicas})
    return Grade(all(check["passed"] for check in checks), tuple(checks))
