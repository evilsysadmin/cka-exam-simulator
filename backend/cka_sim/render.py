"""Trusted Kubernetes manifests derived only from validated safe parameters."""
import yaml
from .models import (
    Scenario, HEALTHY_CPU_REQUEST, HEALTHY_CPU_LIMIT,
    BROKEN_CPU_REQUEST, BROKEN_CPU_LIMIT,
)

IMAGE = "ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine@sha256:e234d4f7e346c098f7b22245690372382aded5be988611eb2c53ae62231eece7"


def fixture_objects(s: Scenario) -> list[dict]:
    labels = {"app": s.target_selector}
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": s.namespace}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
            "name": s.deployment, "namespace": s.namespace}, "spec": {
                "replicas": s.initial_replicas,
                "selector": {"matchLabels": labels},
                "template": {"metadata": {"labels": labels}, "spec": {"containers": [{
                    "name": "web", "image": IMAGE, "ports": [{"containerPort": 8080}],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "runAsNonRoot": True, "runAsUser": 101, "runAsGroup": 101,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {
                        "cpu": BROKEN_CPU_REQUEST if s.scenario_type == "deployment_resources"
                        else HEALTHY_CPU_REQUEST, "memory": "32Mi"},
                                  "limits": {
                        "cpu": BROKEN_CPU_LIMIT if s.scenario_type == "deployment_resources"
                        else HEALTHY_CPU_LIMIT, "memory": "128Mi"}},
                }], "automountServiceAccountToken": False}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {
            "name": s.service, "namespace": s.namespace}, "spec": {
                "selector": {"app": s.initial_selector},
                "ports": [{"port": 80, "targetPort": 8080}],
            }},
    ]


def render_yaml(s: Scenario) -> str:
    return yaml.safe_dump_all(fixture_objects(s), sort_keys=True)
