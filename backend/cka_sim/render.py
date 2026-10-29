"""Trusted Kubernetes manifests derived only from validated safe parameters."""
import yaml
from .models import Scenario

IMAGE = "nginx:1.27-alpine"


def fixture_objects(s: Scenario) -> list[dict]:
    labels = {"app": s.target_selector}
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": s.namespace}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
            "name": s.deployment, "namespace": s.namespace}, "spec": {
                "replicas": s.initial_replicas,
                "selector": {"matchLabels": labels},
                "template": {"metadata": {"labels": labels}, "spec": {"containers": [{
                    "name": "web", "image": IMAGE, "ports": [{"containerPort": 80}],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                  "limits": {"cpu": "250m", "memory": "128Mi"}},
                }], "automountServiceAccountToken": False}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {
            "name": s.service, "namespace": s.namespace}, "spec": {
                "selector": {"app": s.initial_selector},
                "ports": [{"port": 80, "targetPort": 80}],
            }},
    ]


def render_yaml(s: Scenario) -> str:
    return yaml.safe_dump_all(fixture_objects(s), sort_keys=True)
