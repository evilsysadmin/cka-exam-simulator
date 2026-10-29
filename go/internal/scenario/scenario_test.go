package scenario

import (
    "encoding/json"
    "os"
    "path/filepath"
    "reflect"
    "strings"
    "testing"
)

const good = "{\"schema_version\":1,\"slug\":\"scale-payment-api\",\"title\":\"Caída de capacidad en pagos\",\"story\":\"La plataforma necesita recuperar la capacidad pactada sin sustituir el workload.\",\"difficulty\":\"easy\",\"minutes\":6,\"namespace\":\"cka-payments\",\"deployment\":\"payment-api\",\"scenario_type\":\"deployment_scale\",\"initial_replicas\":1,\"target_replicas\":3,\"initial_selector\":\"payment-api\",\"target_selector\":\"payment-api\"}"

func example(t *testing.T) Scenario {
    t.Helper()
    s, err := Parse([]byte(good))
    if err != nil { t.Fatal(err) }
    return s
}

func TestCuratedBankParsesExistingScenarios(t *testing.T) {
    bank, err := LoadBank("../../../scenarios")
    if err != nil { t.Fatal(err) }
    want := []string{"fix-catalog-service", "recover-scheduler-capacity", "scale-payment-api"}
    if !reflect.DeepEqual(Slugs(bank), want) {
        t.Fatalf("curated bank changed: got %v, want %v", Slugs(bank), want)
    }
    if bank["recover-scheduler-capacity"].ScenarioType != DeploymentResources {
        t.Fatal("CPU resource scenario must validate")
    }
    if bank["fix-catalog-service"].ScenarioType != ServiceSelector {
        t.Fatal("service selector scenario must validate")
    }
}

func TestSchemaStrictness(t *testing.T) {
    tests := []struct {
        name string
        edit func(map[string]any)
    }{
        {"unknown executable field", func(m map[string]any) { m["command"] = "rm -rf /" }},
        {"unknown Kubernetes manifests", func(m map[string]any) { m["manifest"] = map[string]any{"kind": "Pod"} }},
        {"boolean replicas", func(m map[string]any) { m["initial_replicas"] = true }},
        {"fractional minutes", func(m map[string]any) { m["minutes"] = 6.5 }},
        {"negative minutes", func(m map[string]any) { m["minutes"] = -1 }},
        {"invalid version", func(m map[string]any) { m["schema_version"] = 0 }},
        {"boolean version", func(m map[string]any) { m["schema_version"] = true }},
        {"null version", func(m map[string]any) { m["schema_version"] = nil }},
        {"missing required slug", func(m map[string]any) { delete(m, "slug") }},
        {"short title", func(m map[string]any) { m["title"] = "bad" }},
        {"long story", func(m map[string]any) { m["story"] = strings.Repeat("a", 321) }},
        {"unknown difficulty", func(m map[string]any) { m["difficulty"] = "expert" }},
        {"unknown type", func(m map[string]any) { m["scenario_type"] = "arbitrary_script" }},
        {"unsafe namespace", func(m map[string]any) { m["namespace"] = "default" }},
        {"unsafe deployment name", func(m map[string]any) { m["deployment"] = "../../api" }},
        {"unsafe selector", func(m map[string]any) { m["target_selector"] = "api;rm" }},
        {"scale starts solved", func(m map[string]any) { m["target_replicas"] = 1 }},
        {"scale selector changes", func(m map[string]any) { m["target_selector"] = "other-pods" }},
        {"service starts solved", func(m map[string]any) { m["scenario_type"] = ServiceSelector }},
        {"service changes replicas", func(m map[string]any) {
            m["scenario_type"] = ServiceSelector
            m["initial_selector"] = "legacy-pods"
        }},
        {"resources changes replicas", func(m map[string]any) { m["scenario_type"] = DeploymentResources }},
        {"resources changes selectors", func(m map[string]any) {
            m["scenario_type"] = DeploymentResources
            m["initial_replicas"] = 3
            m["initial_selector"] = "legacy-pods"
        }},
        {"script injection", func(m map[string]any) { m["story"] = "Please <script>alert(1)</script> run kubectl." }},
        {"shell injection", func(m map[string]any) { m["story"] = "Copy this sudo kubectl command now and run it." }},
        {"URL injection", func(m map[string]any) { m["title"] = "Visit https://unsafe.example for help" }},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            var values map[string]any
            if err := json.Unmarshal([]byte(good), &values); err != nil { t.Fatal(err) }
            tt.edit(values)
            data, err := json.Marshal(values)
            if err != nil { t.Fatal(err) }
            if _, err := Parse(data); err == nil {
                t.Fatalf("accepted prohibited scenario: %s", tt.name)
            }
        })
    }
}

func TestVersionOmissionMatchesPydanticDefault(t *testing.T) {
    var data map[string]any
    if err := json.Unmarshal([]byte(good), &data); err != nil { t.Fatal(err) }
    delete(data, "schema_version")
    raw, _ := json.Marshal(data)
    s, err := Parse(raw)
    if err != nil { t.Fatal(err) }
    if s.SchemaVersion != 1 { t.Fatal("missing schema version should default to 1") }
}

func TestRejectNonObjectAndTrailingJSON(t *testing.T) {
    for _, raw := range []string{"null", "[]", "\"hello\"", "{} true", good + " {}", "", "{invalid}"} {
        if _, err := Parse([]byte(raw)); err == nil { t.Fatalf("accepted invalid JSON: %q", raw) }
    }
}

func TestCuratedBankGuardsFilenameAndNamespace(t *testing.T) {
    dir := t.TempDir()
    if _, err := LoadBank(dir); err == nil { t.Fatal("empty bank accepted") }
    if _, err := LoadBank(filepath.Join(dir, "missing")); err == nil { t.Fatal("missing bank accepted") }
    if err := os.WriteFile(filepath.Join(dir, "wrong-name.json"), []byte(good), 0600); err != nil { t.Fatal(err) }
    if _, err := LoadBank(dir); err == nil { t.Fatal("filename mismatch accepted") }
    if err := os.Rename(filepath.Join(dir, "wrong-name.json"),
        filepath.Join(dir, "scale-payment-api.json")); err != nil { t.Fatal(err) }
    other := example(t)
    other.Slug = "another-scenario"
    other.Namespace = "cka-payments"
    raw, _ := json.Marshal(other)
    if err := os.WriteFile(filepath.Join(dir, "another-scenario.json"), raw, 0600); err != nil { t.Fatal(err) }
    if _, err := LoadBank(dir); err == nil { t.Fatal("duplicate namespace accepted") }
}

func TestCuratedBankRejectsSymlinkedEntries(t *testing.T) {
    dir := t.TempDir()
    original := filepath.Join(dir, "original.txt")
    if err := os.WriteFile(original, []byte(good), 0600); err != nil { t.Fatal(err) }
    if err := os.Symlink(original, filepath.Join(dir, "scale-payment-api.json")); err != nil {
        t.Skipf("symlink not supported: %v", err)
    }
    if _, err := LoadBank(dir); err == nil { t.Fatal("symlinked curated entry accepted") }
}
