package grading

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

type fixture struct {
	Name           string         `json:"name"`
	ScenarioSlug   string         `json:"scenario_slug"`
	Deployment     map[string]any `json:"deployment"`
	Service        map[string]any `json:"service"`
	EndpointSlices map[string]any `json:"endpoint_slices"`
	ExpectedPassed bool           `json:"expected_passed"`
}

func TestOfflineGradingSnapshots(t *testing.T) {
	bank, err := scenario.LoadBank("../../../scenarios")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "grading-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var samples []fixture
	if err := json.Unmarshal(raw, &samples); err != nil {
		t.Fatal(err)
	}
	if len(samples) < 25 {
		t.Fatalf("expected representative positive and negative states; have %d", len(samples))
	}
	seen := map[string]bool{}
	for _, sample := range samples {
		t.Run(sample.Name, func(t *testing.T) {
			if seen[sample.Name] {
				t.Fatal("duplicate snapshot name")
			}
			seen[sample.Name] = true
			s, ok := bank[sample.ScenarioSlug]
			if !ok {
				t.Fatal("unknown curated scenario")
			}
			got := State(s, sample.Deployment, sample.Service, sample.EndpointSlices)
			if got.Passed != sample.ExpectedPassed {
				t.Fatalf("grade for %s = %v, expected %v", sample.Name, got, sample.ExpectedPassed)
			}
			if got.Score != 0 && got.Score != 100 {
				t.Fatal("grade must be binary")
			}
			if (got.Score == 100) != got.Passed {
				t.Fatal("score does not match passed flag")
			}
			want := 2
			if s.ScenarioType == scenario.ServiceSelector {
				want = 3
			} else if s.ScenarioType == scenario.DeploymentResources {
				want = 4
			}
			if len(got.Checks) != want {
				t.Fatalf("expected %d checks, got %d", want, len(got.Checks))
			}
		})
	}
}

func TestRejectsNonKubernetesNumericAndBooleanReady(t *testing.T) {
	bank, err := scenario.LoadBank("../../../scenarios")
	if err != nil {
		t.Fatal(err)
	}
	s := bank["scale-payment-api"]
	d := map[string]any{"spec": map[string]any{"replicas": "3"},
		"status": map[string]any{"availableReplicas": true}}
	if State(s, d, nil, nil).Passed {
		t.Fatal("strings and booleans must not satisfy numeric replica checks")
	}
	s = bank["fix-catalog-service"]
	svc := map[string]any{"spec": map[string]any{"selector": map[string]any{"app": s.TargetSelector}}}
	readyAsInt := map[string]any{"items": []any{map[string]any{"endpoints": []any{
		map[string]any{"conditions": map[string]any{"ready": float64(1)}},
	}}}}
	if State(s, map[string]any{"spec": map[string]any{"replicas": float64(2)}},
		svc, readyAsInt).Passed {
		t.Fatal("numeric endpoint readiness must not count")
	}
}

func TestUnknownScenarioTypeFailsClosed(t *testing.T) {
	s := scenario.Scenario{ScenarioType: "unknown"}
	got := State(s, map[string]any{}, nil, nil)
	if got.Passed || got.Score != 0 || len(got.Checks) != 1 {
		t.Fatalf("future scenario type must not be graded as passed: %+v", got)
	}
}
