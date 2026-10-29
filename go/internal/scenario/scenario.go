// Package scenario defines the strict, non-executable CKA scenario contract.
//
// This is an independent Go implementation of the existing Python/Pydantic
// DSL. No Kubernetes clients, shell commands, prompt execution or publication
// are part of this package.
package scenario

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

type Scenario struct {
	SchemaVersion   int    `json:"schema_version"`
	Slug            string `json:"slug"`
	Title           string `json:"title"`
	Story           string `json:"story"`
	Difficulty      string `json:"difficulty"`
	Minutes         int    `json:"minutes"`
	Namespace       string `json:"namespace"`
	Deployment      string `json:"deployment"`
	ScenarioType    string `json:"scenario_type"`
	InitialReplicas int    `json:"initial_replicas"`
	TargetReplicas  int    `json:"target_replicas"`
	InitialSelector string `json:"initial_selector"`
	TargetSelector  string `json:"target_selector"`
}

const (
	DeploymentScale     = "deployment_scale"
	ServiceSelector     = "service_selector"
	DeploymentResources = "deployment_resources"
)

var (
	resourceName = regexp.MustCompile(`^[a-z][a-z0-9-]*[a-z0-9]$`)
	namespaceName = regexp.MustCompile(`^cka-[a-z][a-z0-9-]*[a-z0-9]$`)
	forbiddenText = []string{"```", "http://", "https://", "<script", "</", "sudo ", "curl ", "wget "}
)

func lengthBetween(text string, min, max int) bool {
	count := utf8.RuneCountInString(text)
	return utf8.ValidString(text) && count >= min && count <= max
}

func (s Scenario) Validate() error {
	// Pydantic's schema_version defaults to 1 if omitted; zero here denotes
	// that same omission, never a JSON value of zero (handled during decode).
	if s.SchemaVersion != 1 {
		return errors.New("unsupported schema_version")
	}
	if !lengthBetween(s.Slug, 3, 44) || !resourceName.MatchString(s.Slug) {
		return errors.New("invalid slug")
	}
	if !lengthBetween(s.Title, 8, 100) || !lengthBetween(s.Story, 12, 320) {
		return errors.New("invalid display text length")
	}
	switch s.Difficulty {
	case "easy", "medium", "hard":
	default:
		return errors.New("invalid difficulty")
	}
	if s.Minutes < 3 || s.Minutes > 20 {
		return errors.New("invalid minutes")
	}
	if !lengthBetween(s.Namespace, 7, 55) || !namespaceName.MatchString(s.Namespace) {
		return errors.New("invalid namespace")
	}
	if !lengthBetween(s.Deployment, 3, 45) || !resourceName.MatchString(s.Deployment) {
		return errors.New("invalid deployment")
	}
	for _, selector := range []string{s.InitialSelector, s.TargetSelector} {
		if !lengthBetween(selector, 3, 45) || !resourceName.MatchString(selector) {
			return errors.New("invalid selector")
		}
	}
	if s.InitialReplicas < 1 || s.InitialReplicas > 3 ||
		s.TargetReplicas < 1 || s.TargetReplicas > 4 {
		return errors.New("invalid replicas")
	}
	switch s.ScenarioType {
	case DeploymentScale:
		if s.InitialReplicas == s.TargetReplicas ||
			s.InitialSelector != s.TargetSelector {
			return errors.New("scale scenario has no isolated replica fault")
		}
	case ServiceSelector:
		if s.InitialSelector == s.TargetSelector ||
			s.InitialReplicas != s.TargetReplicas {
			return errors.New("service scenario has no isolated selector fault")
		}
	case DeploymentResources:
		if s.InitialReplicas != s.TargetReplicas ||
			s.InitialSelector != s.TargetSelector {
			return errors.New("CPU scenario must preserve replicas and selectors")
		}
	default:
		return errors.New("invalid scenario_type")
	}
	text := strings.ToLower(s.Title + " " + s.Story)
	for _, fragment := range forbiddenText {
		if strings.Contains(text, fragment) {
			return errors.New("scenario display text contains forbidden elements")
		}
	}
	return nil
}

// Parse enforces unknown-field rejection, exact JSON types, one document,
// and the same schema_version=1 omission default as the Python model.
func Parse(data []byte) (Scenario, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return Scenario{}, errors.New("invalid JSON object")
	}
	if fields == nil {
		return Scenario{}, errors.New("expected a JSON object")
	}
	s := Scenario{SchemaVersion: 1}
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&s); err != nil {
		return Scenario{}, errors.New("invalid scenario fields")
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return Scenario{}, errors.New("trailing JSON data")
	}
	// Explicit null and zero versions are invalid; missing is the only default.
	if raw, exists := fields["schema_version"]; exists &&
		string(raw) != "1" {
		return Scenario{}, errors.New("unsupported schema_version")
	}
	if err := s.Validate(); err != nil {
		return Scenario{}, err
	}
	return s, nil
}

// LoadBank reads only top-level curated JSON files. The bank must exist and
// contain at least one scenario; generated YAML is never published here.
func LoadBank(directory string) (map[string]Scenario, error) {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, errors.New("curated scenario bank unavailable")
	}
	found := make(map[string]Scenario)
	namespaces := make(map[string]bool)
	for _, entry := range entries {
		if filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		if !entry.Type().IsRegular() {
			return nil, fmt.Errorf("curated entry %q is not a regular file", entry.Name())
		}
		data, err := os.ReadFile(filepath.Join(directory, entry.Name()))
		if err != nil {
			return nil, fmt.Errorf("cannot read curated scenario %q", entry.Name())
		}
		s, err := Parse(data)
		if err != nil {
			return nil, fmt.Errorf("invalid curated scenario %q: %w", entry.Name(), err)
		}
		if strings.TrimSuffix(entry.Name(), ".json") != s.Slug {
			return nil, fmt.Errorf("curated filename mismatch %q", entry.Name())
		}
		if _, exists := found[s.Slug]; exists || namespaces[s.Namespace] {
			return nil, errors.New("duplicate curated slug or namespace")
		}
		found[s.Slug] = s
		namespaces[s.Namespace] = true
	}
	if len(found) == 0 {
		return nil, errors.New("curated scenario bank is empty")
	}
	return found, nil
}

func Slugs(bank map[string]Scenario) []string {
	slugs := make([]string, 0, len(bank))
	for slug := range bank {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	return slugs
}
