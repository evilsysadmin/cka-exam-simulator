// Package grading evaluates read-only Kubernetes object snapshots.
// It never invokes kubectl, executes candidate commands or publishes scenarios.
package grading

import (
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

type Check struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
}

type Grade struct {
	Passed bool    `json:"passed"`
	Checks []Check `json:"checks"`
	Score  int     `json:"score"`
}

func field(value any, key string) any {
	object, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	return object[key]
}

func number(value any) (int64, bool) {
	// encoding/json uses float64 for whole Kubernetes JSON numbers.
	// Never interpret true as one or coerce strings to numbers.
	n, ok := value.(float64)
	if !ok || n != float64(int64(n)) {
		return 0, false
	}
	return int64(n), true
}

func equalsNumber(value any, expected int) bool {
	n, ok := number(value)
	return ok && n == int64(expected)
}

func atLeast(value any, expected int) bool {
	n, ok := number(value)
	return ok && n >= int64(expected)
}

func equalString(value any, expected string) bool {
	actual, ok := value.(string)
	return ok && actual == expected
}

func containersForWeb(deployment any) any {
	list, ok := field(field(field(field(deployment, "spec"), "template"), "spec"), "containers").([]any)
	if !ok {
		return nil
	}
	for _, container := range list {
		if equalString(field(container, "name"), "web") {
			return container
		}
	}
	return nil
}

func matchingSelector(service any, target string) bool {
	selector, ok := field(field(service, "spec"), "selector").(map[string]any)
	if !ok || len(selector) != 1 {
		return false
	}
	return equalString(selector["app"], target)
}

func readyBackends(slices any) int {
	items, ok := field(slices, "items").([]any)
	if !ok {
		return 0
	}
	count := 0
	for _, item := range items {
		endpoints, ok := field(item, "endpoints").([]any)
		if !ok {
			continue
		}
		for _, endpoint := range endpoints {
			ready, ok := field(field(endpoint, "conditions"), "ready").(bool)
			if ok && ready {
				count++
			}
		}
	}
	return count
}

func rolloutCurrent(deployment any, status any, target int) bool {
	generation, ok := number(field(field(deployment, "metadata"), "generation"))
	if !ok {
		return false
	}
	observed, ok := number(field(status, "observedGeneration"))
	return ok && observed >= generation &&
		atLeast(field(status, "updatedReplicas"), target) &&
		atLeast(field(status, "availableReplicas"), target)
}

// State grades observed Kubernetes API snapshots, mirroring the Python grader.
// It is not a simulation of the cluster, and never trusts an LLM as judge.
// Callers must pass previously validated curated scenarios.
func State(s scenario.Scenario, deployment, service, endpointSlices map[string]any) Grade {
	spec := field(deployment, "spec")
	status := field(deployment, "status")
	checks := make([]Check, 0, 4)
	add := func(name string, passed bool) {
		checks = append(checks, Check{Name: name, Passed: passed})
	}
	switch s.ScenarioType {
	case scenario.DeploymentScale:
		add("Réplicas declaradas", equalsNumber(field(spec, "replicas"), s.TargetReplicas))
		// Unlike declared replicas, a missing availability field means zero.
		available := field(status, "availableReplicas")
		if available == nil {
			available = float64(0)
		}
		add("Réplicas disponibles", atLeast(available, s.TargetReplicas))
	case scenario.DeploymentResources:
		resources := field(containersForWeb(deployment), "resources")
		add("CPU solicitado", equalString(field(field(resources, "requests"), "cpu"), "10m"))
		add("Límite de CPU", equalString(field(field(resources, "limits"), "cpu"), "250m"))
		add("Réplicas declaradas", equalsNumber(field(spec, "replicas"), s.TargetReplicas))
		// Ready old ReplicaSet pods are insufficient: rollout must be observed.
		add("Nuevo rollout disponible", rolloutCurrent(deployment, status, s.TargetReplicas))
	case scenario.ServiceSelector:
		add("Selector del Service", matchingSelector(service, s.TargetSelector))
		add("Backends listos", readyBackends(endpointSlices) >= 1)
		add("Deployment conservado", equalsNumber(field(spec, "replicas"), s.TargetReplicas))
	default:
		// Fail closed if a future scenario type reaches an unupgraded grader.
		add("Tipo no admitido", false)
	}
	result := Grade{Passed: true, Checks: checks}
	for _, check := range checks {
		if !check.Passed {
			result.Passed = false
		}
	}
	if result.Passed {
		result.Score = 100
	}
	return result
}
