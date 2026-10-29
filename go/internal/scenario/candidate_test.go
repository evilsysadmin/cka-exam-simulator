package scenario

import (
    "encoding/json"
    "reflect"
    "strconv"
    "strings"
    "testing"
)

func TestCuratedCandidateViewsHaveOnlyApprovedFields(t *testing.T) {
    bank, err := LoadBank("../../../scenarios")
    if err != nil { t.Fatal(err) }
    views, err := CandidateBank(bank)
    if err != nil { t.Fatal(err) }
    if len(views) != 3 { t.Fatalf("expected three scenarios, got %d", len(views)) }
    wantKeys := map[string]bool{
        "slug": true, "title": true, "story": true, "difficulty": true,
        "minutes": true, "domain": true, "statement": true, "namespace": true,
    }
    for i, view := range views {
        if i > 0 && views[i-1].Slug >= view.Slug { t.Fatal("views are not sorted") }
        raw, err := json.Marshal(view)
        if err != nil { t.Fatal(err) }
        var actual map[string]any
        if err := json.Unmarshal(raw, &actual); err != nil { t.Fatal(err) }
        if len(actual) != len(wantKeys) {
            t.Fatalf("private data present or required key missing in %s: %s", view.Slug, raw)
        }
        for key := range actual {
            if !wantKeys[key] { t.Fatalf("private field %q disclosed in %s", key, raw) }
        }
        for _, internal := range []string{
            "initial_replicas", "target_replicas", "initial_selector",
            "target_selector", "deployment", "schema_version", "scenario_type",
            "grader", "manifest", "solution",
        } {
            if strings.Contains(string(raw), strconv.Quote(internal)) {
                t.Fatalf("private key %q leaked in view: %s", internal, raw)
            }
        }
        if view.Namespace != bank[view.Slug].Namespace { t.Fatal("candidate namespace changed") }
        if view.Statement == "" { t.Fatal("candidate task statement missing") }
    }
}

func TestCandidateStatementsTypeSpecific(t *testing.T) {
    bank, err := LoadBank("../../../scenarios")
    if err != nil { t.Fatal(err) }
    want := map[string]struct{ domain, statement string }{
        "scale-payment-api": {
            "workloads",
            "En el namespace cka-payments, ajusta el Deployment payment-api a 3 réplicas disponibles.",
        },
        "fix-catalog-service": {
            "networking",
            "En cka-catalog, repara el Service catalog-api-svc para enrutar tráfico a los pods del Deployment catalog-api. No alteres las réplicas ni elimines el Service.",
        },
        "recover-scheduler-capacity": {
            "workloads",
            "En cka-scheduling, los pods del Deployment batch-api no se pueden programar por una petición de CPU excesiva. Ajusta el contenedor web a requests.cpu=10m y limits.cpu=250m, sin cambiar el número de réplicas ni el selector. Deja todas las réplicas disponibles.",
        },
    }
    for slug, expected := range want {
        got, err := Candidate(bank[slug])
        if err != nil { t.Fatal(err) }
        if got.Domain != expected.domain || got.Statement != expected.statement {
            t.Fatalf("%s unexpected candidate view: %#v", slug, got)
        }
    }
}

func TestCandidateRejectsInvalidAndUntrustedObjects(t *testing.T) {
    bank, err := LoadBank("../../../scenarios")
    if err != nil { t.Fatal(err) }
    testCases := map[string]func(*Scenario){
        "unsafe story": func(s *Scenario) { s.Story = "Please run sudo kubectl commands now." },
        "invalid scenario type": func(s *Scenario) { s.ScenarioType = "host_exec" },
        "solved initial state": func(s *Scenario) { s.InitialReplicas = s.TargetReplicas },
        "bad namespace": func(s *Scenario) { s.Namespace = "kube-system" },
        "wrong schema version": func(s *Scenario) { s.SchemaVersion = 999 },
    }
    for name, mutate := range testCases {
        t.Run(name, func(t *testing.T) {
            s := bank["scale-payment-api"]
            mutate(&s)
            if _, err := Candidate(s); err == nil { t.Fatal("untrusted scenario accepted") }
        })
    }
    if _, err := CandidateBank(nil); err == nil { t.Fatal("empty bank must fail closed") }
    s := bank["scale-payment-api"]
    if _, err := CandidateBank(map[string]Scenario{"incorrect-key": s}); err == nil {
        t.Fatal("inconsistent slug key accepted")
    }
}

func TestCandidateBankMatchesIndividualProjection(t *testing.T) {
    bank, err := LoadBank("../../../scenarios")
    if err != nil { t.Fatal(err) }
    views, err := CandidateBank(bank)
    if err != nil { t.Fatal(err) }
    for i, slug := range Slugs(bank) {
        one, err := Candidate(bank[slug])
        if err != nil { t.Fatal(err) }
        if !reflect.DeepEqual(views[i], one) { t.Fatalf("mismatched projection: %s", slug) }
    }
}
