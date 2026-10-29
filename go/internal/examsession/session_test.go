package examsession

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

const (
	alice = "11111111-1111-4111-8111-111111111111"
	bob   = "22222222-2222-4222-8222-222222222222"
)

func curated(t *testing.T) scenario.Scenario {
	t.Helper()
	bank, err := scenario.LoadBank("../../../scenarios")
	if err != nil {
		t.Fatal(err)
	}
	return bank["scale-payment-api"]
}

func clockStore() (*Store, *time.Time) {
	value := time.Date(2026, time.October, 29, 22, 0, 0, 0, time.UTC)
	return newStore(func() time.Time { return value }, rand.Reader, 3), &value
}

func TestLeaseIsUniqueScopedAndOwnerStatusDoesNotLeakInternals(t *testing.T) {
	store, _ := clockStore()
	base := curated(t)
	first, err := store.Start(alice, base)
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.Start(bob, base)
	if err != nil {
		t.Fatal(err)
	}
	if first.ID == second.ID || first.Namespace == second.Namespace ||
		first.Namespace == base.Namespace || second.Namespace == base.Namespace {
		t.Fatal("cross-candidate session/namespace collision")
	}
	if len(first.ID) != 32 || !regexp.MustCompile("^cka-lab-[a-f0-9]{32}$").MatchString(first.Namespace) {
		t.Fatal("invalid random session id or namespace")
	}
	if first.Scenario.Namespace != first.Namespace || second.Scenario.Namespace != second.Namespace {
		t.Fatal("scoped scenario does not match lease namespace")
	}
	if base.Namespace == first.Scenario.Namespace || first.Scenario.Slug != base.Slug {
		t.Fatal("curated source scenario was mutated")
	}
	if err := first.Scenario.Validate(); err != nil {
		t.Fatal(err)
	}
	view, err := store.Status(alice, first.ID)
	if err != nil || view.Phase != Active || view.RemainingSeconds != base.Minutes*60 {
		t.Fatalf("unexpected live session status %+v err=%v", view, err)
	}
	data, err := json.Marshal(view)
	if err != nil {
		t.Fatal(err)
	}
	for _, fragment := range []string{"namespace", "owner", alice, first.Namespace,
		"initial_selector", "initial_replicas", "target_replicas", "deployment"} {
		if strings.Contains(string(data), fragment) {
			t.Fatalf("public status leaked %s: %s", fragment, data)
		}
	}
	if got, err := store.ActiveScenario(alice, first.ID); err != nil || got.Namespace != first.Namespace {
		t.Fatal("trusted owner cannot retrieve active scoped scenario")
	}
}

func TestWrongOwnerDeniedIdenticallyToUnknownLease(t *testing.T) {
	store, _ := clockStore()
	lease, _ := store.Start(alice, curated(t))
	wrongID := strings.Repeat("f", 32)
	foreign, errForeign := store.Status(bob, lease.ID)
	unknown, errUnknown := store.Status(bob, wrongID)
	if !errors.Is(errForeign, ErrDenied) || errForeign != errUnknown || foreign != unknown {
		t.Fatal("foreign and unknown IDs are observably different")
	}
	if _, err := store.ActiveScenario(bob, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign session scenario disclosed")
	}
	if _, err := store.Finish(bob, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign owner was able to finish the lease")
	}
	if _, err := store.Status(alice, ""); !errors.Is(err, ErrDenied) {
		t.Fatal("invalid session ID accepted")
	}
	if _, err := store.Finish("invalid-owner", lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("invalid owner accepted")
	}
}

func TestExpiryPreventsAccessAndRequiresConfirmedDeletion(t *testing.T) {
	store, now := clockStore()
	s := curated(t)
	lease, err := store.Start(alice, s)
	if err != nil {
		t.Fatal(err)
	}
	*now = now.Add(time.Duration(s.Minutes)*time.Minute - time.Millisecond)
	justBefore, err := store.Status(alice, lease.ID)
	if err != nil || justBefore.Phase != Active || justBefore.RemainingSeconds != 1 {
		t.Fatalf("expected final second: %+v, %v", justBefore, err)
	}
	*now = now.Add(time.Millisecond)
	state, err := store.Status(alice, lease.ID)
	if err != nil || state.Phase != Expired || state.RemainingSeconds != 0 {
		t.Fatalf("deadline not enforced: %+v %v", state, err)
	}
	if _, err := store.ActiveScenario(alice, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("expired exam can still provision a cluster")
	}
	if _, err := store.Start(alice, s); !errors.Is(err, ErrConflict) {
		t.Fatal("expired but uncleaned lease released owner prematurely")
	}
	if err := store.ConfirmCleanup(Cleanup{ID: lease.ID, Namespace: "cka-other"}); !errors.Is(err, ErrDenied) {
		t.Fatal("wrong namespace cleanup acknowledged")
	}
	pending := store.CleanupPending()
	if len(pending) != 1 || pending[0].Namespace != lease.Namespace {
		t.Fatalf("expected cleanup obligation for expired namespace: %#v", pending)
	}
	if err := store.ConfirmCleanup(pending[0]); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Status(alice, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("destroyed session remains visible")
	}
	if _, err := store.Start(alice, s); err != nil {
		t.Fatalf("confirmed deletion should free the owner: %v", err)
	}
}

func TestFinishQueuesCleanupWithoutDeletingResources(t *testing.T) {
	store, _ := clockStore()
	lease, _ := store.Start(alice, curated(t))
	if err := store.ConfirmCleanup(Cleanup{ID: lease.ID, Namespace: lease.Namespace}); !errors.Is(err, ErrDenied) {
		t.Fatal("active session must not be acknowledged as clean")
	}
	if len(store.CleanupPending()) != 0 {
		t.Fatal("active session should not appear in cleanup queue")
	}
	clean, err := store.Finish(alice, lease.ID)
	if err != nil || clean.ID != lease.ID || clean.Namespace != lease.Namespace {
		t.Fatalf("invalid pending cleanup %v %+v", err, clean)
	}
	cleanAgain, err := store.Finish(alice, lease.ID)
	if err != nil || cleanAgain != clean {
		t.Fatal("finish must be idempotent for the owner")
	}
	view, _ := store.Status(alice, lease.ID)
	if view.Phase != Closed || view.RemainingSeconds != 0 {
		t.Fatal("closed session is still active")
	}
	if _, err := store.ActiveScenario(alice, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("closed session still has an active scoped scenario")
	}
	if _, err := store.Start(alice, curated(t)); !errors.Is(err, ErrConflict) {
		t.Fatal("finished session was prematurely replaced")
	}
	if err := store.ConfirmCleanup(clean); err != nil {
		t.Fatal(err)
	}
	if err := store.ConfirmCleanup(clean); !errors.Is(err, ErrDenied) {
		t.Fatal("duplicate cleanup acknowledgement succeeded")
	}
}

func TestRejectInvalidOwnerScenarioEntropyAndCapacity(t *testing.T) {
	store, _ := clockStore()
	s := curated(t)
	for _, owner := range []string{"", "not-a-uuid", strings.ToUpper("abcdefab-1234-4abc-8abc-abcdefabcdef"),
		"11111111-1111-1111-8111-111111111111"} {
		if _, err := store.Start(owner, s); !errors.Is(err, ErrInvalid) {
			t.Fatalf("invalid owner %q accepted: %v", owner, err)
		}
	}
	bad := s
	bad.Namespace = "default"
	if _, err := store.Start(alice, bad); !errors.Is(err, ErrInvalid) {
		t.Fatal("invalid scenario accepted")
	}
	shortEntropy := newStore(time.Now, bytes.NewReader([]byte("only-7")), 2)
	if _, err := shortEntropy.Start(alice, s); !errors.Is(err, ErrInvalid) {
		t.Fatal("partial random bytes accepted")
	}
	collisionEntropy := newStore(time.Now, bytes.NewReader(make([]byte, 64)), 3)
	if _, err := collisionEntropy.Start(alice, s); err != nil {
		t.Fatal(err)
	}
	if _, err := collisionEntropy.Start(bob, s); !errors.Is(err, ErrConflict) {
		t.Fatal("random ID collision accepted")
	}
	if _, err := store.Start(alice, s); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Start(alice, s); !errors.Is(err, ErrConflict) {
		t.Fatal("same owner admitted twice")
	}
	full := newStore(time.Now, rand.Reader, 1)
	if _, err := full.Start(alice, s); err != nil {
		t.Fatal(err)
	}
	if _, err := full.Start(bob, s); !errors.Is(err, ErrCapacity) {
		t.Fatal("capacity bound bypassed")
	}
}

type failedEntropy struct{}
func (failedEntropy) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }

func TestEntropyFailureCannotReserveSession(t *testing.T) {
	store := newStore(time.Now, failedEntropy{}, 2)
	if _, err := store.Start(alice, curated(t)); !errors.Is(err, ErrInvalid) {
		t.Fatal("broken randomness should fail closed")
	}
	if len(store.entries) != 0 || len(store.owners) != 0 {
		t.Fatal("entropy failure left partially reserved session")
	}
}

func TestConcurrentOwnerCannotStartTwoExams(t *testing.T) {
	store := NewMemoryStore()
	s := curated(t)
	const goroutines = 48
	var wait sync.WaitGroup
	wait.Add(goroutines)
	results := make(chan error, goroutines)
	for i := 0; i < goroutines; i++ {
		go func() {
			defer wait.Done()
			_, err := store.Start(alice, s)
			results <- err
		}()
	}
	wait.Wait()
	close(results)
	ok, conflicts := 0, 0
	for err := range results {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrConflict):
			conflicts++
		default:
			t.Fatalf("unexpected concurrent result: %v", err)
		}
	}
	if ok != 1 || conflicts != goroutines-1 {
		t.Fatalf("single-owner concurrency invariant broken: %d successes %d conflicts", ok, conflicts)
	}
}

func TestConcurrentCandidatesNeverShareNamespace(t *testing.T) {
	store := NewMemoryStore()
	s := curated(t)
	const n = 64
	leases := make(chan Lease, n)
	errs := make(chan error, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			owner := fmt.Sprintf("12345678-1234-4%03x-8abc-%012x", i, i+1)
			lease, err := store.Start(owner, s)
			if err != nil {
				errs <- err
				return
			}
			leases <- lease
		}(i)
	}
	wg.Wait()
	close(errs)
	close(leases)
	for err := range errs {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	ids := map[string]bool{}
	for lease := range leases {
		if seen[lease.Namespace] || ids[lease.ID] {
			t.Fatal("two owners share namespace or id")
		}
		seen[lease.Namespace] = true
		ids[lease.ID] = true
	}
	if len(seen) != n {
		t.Fatalf("expected %d distinct candidate namespaces, got %d", n, len(seen))
	}
}

func TestPendingCleanupDeterministicAndNeverReleasesOtherOwner(t *testing.T) {
	store, now := clockStore()
	s := curated(t)
	a, _ := store.Start(alice, s)
	b, _ := store.Start(bob, s)
	_, _ = store.Finish(alice, a.ID)
	*now = now.Add(time.Duration(s.Minutes)*time.Minute + time.Second)
	pending := store.CleanupPending()
	if len(pending) != 2 || !sort.SliceIsSorted(pending, func(i,j int)bool{return pending[i].ID<pending[j].ID}) {
		t.Fatal("expired/closed cleanup obligations are not deterministic")
	}
	for _, obligation := range pending {
		if obligation.ID == a.ID && obligation.Namespace != a.Namespace ||
			obligation.ID == b.ID && obligation.Namespace != b.Namespace {
			t.Fatal("cross-owner cleanup corrupted")
		}
	}
	if err := store.ConfirmCleanup(Cleanup{ID: b.ID, Namespace: a.Namespace}); !errors.Is(err, ErrDenied) {
		t.Fatal("cleanup of foreign namespace succeeded")
	}
	if _, err := store.Start(bob, s); !errors.Is(err, ErrConflict) {
		t.Fatal("pending cleanup freed different owner's slot")
	}
}
