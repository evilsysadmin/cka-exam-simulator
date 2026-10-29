// cka-grade evaluates offline snapshot fixtures without cluster access.
// It exists to compare the new Go grader to the current Python reference.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/grading"
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

type snapshot struct {
	Name           string         `json:"name"`
	ScenarioSlug   string         `json:"scenario_slug"`
	Deployment     map[string]any `json:"deployment"`
	Service        map[string]any `json:"service"`
	EndpointSlices map[string]any `json:"endpoint_slices"`
}

type result struct {
	Name  string        `json:"name"`
	Grade grading.Grade `json:"grade"`
}

func run(bankPath, fixtures string) error {
	bank, err := scenario.LoadBank(bankPath)
	if err != nil {
		return err
	}
	data, err := os.ReadFile(fixtures)
	if err != nil {
		return errors.New("offline grading fixtures unavailable")
	}
	var samples []snapshot
	if err := json.Unmarshal(data, &samples); err != nil || len(samples) == 0 {
		return errors.New("invalid offline grading fixtures")
	}
	output := make([]result, 0, len(samples))
	names := make(map[string]bool)
	for _, sample := range samples {
		s, ok := bank[sample.ScenarioSlug]
		if !ok || sample.Name == "" || names[sample.Name] || sample.Deployment == nil {
			return errors.New("invalid or repeated grading fixture")
		}
		names[sample.Name] = true
		output = append(output, result{
			Name: sample.Name,
			Grade: grading.State(s, sample.Deployment, sample.Service, sample.EndpointSlices),
		})
	}
	return json.NewEncoder(os.Stdout).Encode(output)
}

func main() {
	bank := flag.String("bank", "../scenarios", "curated read-only bank path")
	fixtures := flag.String("fixtures", "testdata/grading-cases.json", "offline snapshot fixtures")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(2)
	}
	if err := run(*bank, *fixtures); err != nil {
		fmt.Fprintln(os.Stderr, "CKA_GO_GRADE_INVALID:", err)
		os.Exit(1)
	}
}
