package scenario

import (
	"errors"
	"fmt"
)

// CandidateView is an explicit allowlist of the fields safe to send to an
// exam candidate. Do not embed Scenario: it contains hidden initial fixtures,
// selectors and validation-only internals.
type CandidateView struct {
	Slug       string `json:"slug"`
	Title      string `json:"title"`
	Story      string `json:"story"`
	Difficulty string `json:"difficulty"`
	Minutes    int    `json:"minutes"`
	Domain     string `json:"domain"`
	Statement  string `json:"statement"`
	Namespace  string `json:"namespace"`
}

// Candidate returns only the approved presentation contract. Validate before
// displaying text, including for scenarios constructed directly in Go.
func Candidate(s Scenario) (CandidateView, error) {
	if err := s.Validate(); err != nil {
		return CandidateView{}, fmt.Errorf("invalid candidate scenario: %w", err)
	}
	view := CandidateView{
		Slug: s.Slug, Title: s.Title, Story: s.Story,
		Difficulty: s.Difficulty, Minutes: s.Minutes,
		Namespace: s.Namespace, Domain: "workloads",
	}
	switch s.ScenarioType {
	case DeploymentScale:
		view.Statement = fmt.Sprintf(
			"En el namespace %s, ajusta el Deployment %s a %d réplicas disponibles.",
			s.Namespace, s.Deployment, s.TargetReplicas)
	case DeploymentResources:
		view.Statement = fmt.Sprintf(
			"En %s, los pods del Deployment %s no se pueden programar por una petición de CPU excesiva. "+
				"Ajusta el contenedor web a requests.cpu=10m y limits.cpu=250m, "+
				"sin cambiar el número de réplicas ni el selector. Deja todas las réplicas disponibles.",
			s.Namespace, s.Deployment)
	case ServiceSelector:
		view.Domain = "networking"
		view.Statement = fmt.Sprintf(
			"En %s, repara el Service %s-svc para enrutar tráfico a los pods del Deployment %s. "+
				"No alteres las réplicas ni elimines el Service.",
			s.Namespace, s.Deployment, s.Deployment)
	default:
		return CandidateView{}, errors.New("unsupported candidate scenario type")
	}
	return view, nil
}

// CandidateBank returns a stable, sorted view. It refuses a missing bank
// instead of emitting an apparently valid empty list.
func CandidateBank(bank map[string]Scenario) ([]CandidateView, error) {
	if len(bank) == 0 {
		return nil, errors.New("empty candidate bank")
	}
	views := make([]CandidateView, 0, len(bank))
	for _, slug := range Slugs(bank) {
		view, err := Candidate(bank[slug])
		if err != nil {
			return nil, fmt.Errorf("invalid candidate bank entry %q: %w", slug, err)
		}
		if slug != view.Slug {
			return nil, errors.New("candidate bank slug mismatch")
		}
		views = append(views, view)
	}
	return views, nil
}
