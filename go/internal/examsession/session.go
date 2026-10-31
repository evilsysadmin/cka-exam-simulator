// Package examsession implements an internal lease state machine and a Linux
// durable journal for trusted controllers.
//
// This package does NOT authenticate callers, provision Kubernetes resources,
// or provide a public session API. The owner UUID must come from a
// separate, fresh ShadowOps Identity authorization decision, NEVER an HTTP
// user-supplied string. No public API may expose Store or CleanupPending.
package examsession

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"math"
	"regexp"
	"sort"
	"sync"
	"time"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

type Phase string

const (
	Active  Phase = "active"
	Expired Phase = "expired"
	Closed  Phase = "closed"
)

var (
	ErrDenied   = errors.New("exam session unavailable")
	ErrConflict = errors.New("candidate already has an uncleaned exam session")
	ErrCapacity = errors.New("exam session capacity reached")
	ErrInvalid  = errors.New("invalid exam session request")
)

const maxEntries = 256

// Owner ids are canonical UUIDv4 identities supplied ONLY by a verified,
// currently authorized ShadowOps Identity boundary.
var ownerUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

type record struct {
	owner    string
	id       string
	scenario scenario.Scenario
	started  time.Time
	deadline time.Time
	phase    Phase
}

// Lease is trusted-controller-only data, not an HTTP candidate response.
// Namespace is random and independent of the curated bank namespace. The
// returned scenario copy must be used consistently for provision and grading.
type Lease struct {
	ID        string
	Namespace string
	Scenario  scenario.Scenario
	ExpiresAt time.Time
}

// View is the deliberately narrow owner-visible session status. It never
// exposes namespace allocation, credentials or private scenario internals.
type View struct {
	ID               string `json:"id"`
	ScenarioSlug     string `json:"scenario_slug"`
	Phase            Phase  `json:"phase"`
	RemainingSeconds int    `json:"remaining_seconds"`
	ExpiresAt        string `json:"expires_at"`
}

// Cleanup is trusted reaper-only data. A namespace MUST be deleted and
// independently verified before ConfirmCleanup releases the owner slot.
type Cleanup struct {
	ID        string
	Namespace string
}

// Store refuses concurrent exams per identity, gives each lease a separate
// unpredictable namespace, and never releases a session before cleanup is
// explicitly confirmed. It is intentionally NOT durable across restarts.
type Store struct {
	mu      sync.Mutex
	now     func() time.Time
	random  io.Reader
	limit   int
	entries map[string]*record
	owners  map[string]string
}

func NewMemoryStore() *Store {
	return newStore(time.Now, rand.Reader, maxEntries)
}

func newStore(now func() time.Time, entropy io.Reader, limit int) *Store {
	return &Store{
		now: now, random: entropy, limit: limit,
		entries: make(map[string]*record), owners: make(map[string]string),
	}
}

func (s *Store) refresh(r *record, now time.Time) {
	if r.phase == Active && !now.Before(r.deadline) {
		r.phase = Expired
	}
}

func (s *Store) view(r *record, now time.Time) View {
	s.refresh(r, now)
	remaining := 0
	if r.phase == Active {
		remaining = int(math.Ceil(r.deadline.Sub(now).Seconds()))
	}
	return View{
		ID: r.id, ScenarioSlug: r.scenario.Slug, Phase: r.phase,
		RemainingSeconds: remaining, ExpiresAt: r.deadline.UTC().Format(time.RFC3339Nano),
	}
}

func randomHex(reader io.Reader) (string, error) {
	var value [16]byte
	if _, err := io.ReadFull(reader, value[:]); err != nil {
		return "", ErrInvalid
	}
	return hex.EncodeToString(value[:]), nil
}

// Start atomically reserves a session and a unique private namespace. A
// missing/broken entropy source, invalid owner/scenario, existing uncleaned
// lease, or exhausted capacity all fail before any lease is published.
func (s *Store) Start(owner string, base scenario.Scenario) (Lease, error) {
	if s == nil || !ownerUUID.MatchString(owner) || base.Validate() != nil {
		return Lease{}, ErrInvalid
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.entries == nil || s.owners == nil || s.random == nil || s.now == nil || s.limit < 1 {
		return Lease{}, ErrInvalid
	}
	if _, exists := s.owners[owner]; exists {
		return Lease{}, ErrConflict
	}
	if len(s.entries) >= s.limit {
		return Lease{}, ErrCapacity
	}
	id, err := randomHex(s.random)
	if err != nil {
		return Lease{}, err
	}
	namespaceSuffix, err := randomHex(s.random)
	if err != nil {
		return Lease{}, err
	}
	if _, exists := s.entries[id]; exists {
		return Lease{}, ErrConflict // no collision retries with a broken RNG
	}
	namespace := "cka-lab-" + namespaceSuffix
	for _, existing := range s.entries {
		if existing.scenario.Namespace == namespace {
			return Lease{}, ErrConflict
		}
	}
	scoped := base
	scoped.Namespace = namespace
	if scoped.Validate() != nil {
		return Lease{}, ErrInvalid
	}
	now := s.now()
	if now.IsZero() {
		return Lease{}, ErrInvalid
	}
	deadline := now.Add(time.Duration(scoped.Minutes) * time.Minute)
	s.entries[id] = &record{
		owner: owner, id: id, scenario: scoped,
		started: now, deadline: deadline, phase: Active,
	}
	s.owners[owner] = id
	return Lease{
		ID: id, Namespace: namespace, Scenario: scoped, ExpiresAt: deadline.UTC(),
	}, nil
}

func (s *Store) owned(owner, id string) (*record, error) {
	if s == nil || !ownerUUID.MatchString(owner) || len(id) != 32 {
		return nil, ErrDenied
	}
	rec, exists := s.entries[id]
	if !exists || rec.owner != owner {
		return nil, ErrDenied
	}
	return rec, nil
}

// Status does not mutate Kubernetes. Both unknown and foreign sessions
// return the exact same error, preventing ownership enumeration.
func (s *Store) Status(owner, id string) (View, error) {
	if s == nil {
		return View{}, ErrDenied
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, err := s.owned(owner, id)
	if err != nil {
		return View{}, err
	}
	return s.view(rec, s.now()), nil
}

// ActiveScenario returns the namespace-scoped scenario only to the owner of
// an active lease, so a future trusted controller can provision its resources.
func (s *Store) ActiveScenario(owner, id string) (scenario.Scenario, error) {
	if s == nil {
		return scenario.Scenario{}, ErrDenied
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, err := s.owned(owner, id)
	if err != nil {
		return scenario.Scenario{}, err
	}
	s.refresh(rec, s.now())
	if rec.phase != Active {
		return scenario.Scenario{}, ErrDenied
	}
	return rec.scenario, nil
}

// Finish queues cleanup, but cannot release the owner while Kubernetes may
// still contain fixtures, tokens or namespaces belonging to this session.
func (s *Store) Finish(owner, id string) (Cleanup, error) {
	if s == nil {
		return Cleanup{}, ErrDenied
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, err := s.owned(owner, id)
	if err != nil {
		return Cleanup{}, err
	}
	rec.phase = Closed
	return Cleanup{ID: rec.id, Namespace: rec.scenario.Namespace}, nil
}

// CleanupPending marks expired leases and reports cleanup obligations. This
// function never deletes Kubernetes resources and must NOT be a public route.
func (s *Store) CleanupPending() []Cleanup {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	pending := make([]Cleanup, 0)
	for _, rec := range s.entries {
		s.refresh(rec, now)
		if rec.phase != Active {
			pending = append(pending, Cleanup{ID: rec.id, Namespace: rec.scenario.Namespace})
		}
	}
	sort.Slice(pending, func(i, j int) bool { return pending[i].ID < pending[j].ID })
	return pending
}

// ConfirmCleanup is trusted-controller-only. The caller must have completed
// AND verified Kubernetes deletion before calling it. Mismatched namespace,
// active leases, or unknown IDs cannot release the candidate slot.
func (s *Store) ConfirmCleanup(clean Cleanup) error {
	if s == nil {
		return ErrDenied
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.entries[clean.ID]
	if !ok || rec.scenario.Namespace != clean.Namespace {
		return ErrDenied
	}
	s.refresh(rec, s.now())
	if rec.phase == Active {
		return ErrDenied
	}
	delete(s.entries, clean.ID)
	if s.owners[rec.owner] == clean.ID {
		delete(s.owners, rec.owner)
	}
	return nil
}
