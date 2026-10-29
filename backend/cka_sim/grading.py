"""Deterministic, read-only checks of Kubernetes resource snapshots."""
from dataclasses import dataclass
from typing import Any
from .models import Scenario, HEALTHY_CPU_REQUEST, HEALTHY_CPU_LIMIT


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
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    if s.scenario_type == "deployment_scale":
        desired = spec.get("replicas")
        available = status.get("availableReplicas", 0)
        checks.append({"name": "Réplicas declaradas", "passed": desired == s.target_replicas})
        checks.append({"name": "Réplicas disponibles", "passed": available >= s.target_replicas})
    elif s.scenario_type == "deployment_resources":
        containers = spec.get("template", {}).get("spec", {}).get("containers") or []
        web = next((c for c in containers if c.get("name") == "web"), {})
        resources = web.get("resources") or {}
        requests = resources.get("requests") or {}
        limits = resources.get("limits") or {}
        checks.append({"name": "CPU solicitado", "passed": requests.get("cpu") == HEALTHY_CPU_REQUEST})
        checks.append({"name": "Límite de CPU", "passed": limits.get("cpu") == HEALTHY_CPU_LIMIT})
        checks.append({"name": "Réplicas declaradas", "passed": spec.get("replicas") == s.target_replicas})
        # Available old ReplicaSet pods do not prove the new rollout completed.
        generation = deployment.get("metadata", {}).get("generation")
        rollout_ready = (isinstance(generation, int)
                         and isinstance(status.get("observedGeneration"), int)
                         and status["observedGeneration"] >= generation
                         and status.get("updatedReplicas", 0) >= s.target_replicas
                         and status.get("availableReplicas", 0) >= s.target_replicas)
        checks.append({"name": "Nuevo rollout disponible", "passed": rollout_ready})
    else:
        selector = (service or {}).get("spec", {}).get("selector")
        slices = (endpoint_slices or {}).get("items") or []
        ready = sum(1 for item in slices for ep in (item.get("endpoints") or [])
                    if ep.get("conditions", {}).get("ready") is True)
        checks.append({"name": "Selector del Service", "passed": selector == {"app": s.target_selector}})
        checks.append({"name": "Backends listos", "passed": ready >= 1})
        checks.append({"name": "Deployment conservado", "passed":
                      spec.get("replicas") == s.target_replicas})
    return Grade(all(check["passed"] for check in checks), tuple(checks))
