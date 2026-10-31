//go:build linux

package examsession

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func journalDir(t *testing.T) string {
	t.Helper()
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	return directory
}

func openTestJournal(t *testing.T, directory string, capacity int) *Journal {
	t.Helper()
	j, err := OpenJournal(directory, capacity)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	return j
}

func TestJournalRecoversActiveLeaseAsCleanupBeforeReadmission(t *testing.T) {
	directory := journalDir(t)
	j := openTestJournal(t, directory, 1)
	lease, err := j.Start(alice, curated(t))
	if err != nil {
		t.Fatal(err)
	}
	view, err := j.Status(alice, lease.ID)
	if err != nil || view.Phase != Active {
		t.Fatal("new lease not active", err)
	}
	if _, err := j.Status(bob, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign status accepted")
	}
	if _, err := j.Finish(bob, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign finish accepted")
	}
	if _, err := j.Start(bob, curated(t)); !errors.Is(err, ErrCapacity) {
		t.Fatal("pilot capacity exceeded")
	}
	j.Close()
	j = openTestJournal(t, directory, 1)
	view, err = j.Status(alice, lease.ID)
	if err != nil || view.Phase != Closed || view.RemainingSeconds != 0 {
		t.Fatal("recovered lease resumed", err)
	}
	if _, err := j.ActiveScenario(alice, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("recovered lease may execute")
	}
	if _, err := j.Start(alice, curated(t)); !errors.Is(err, ErrConflict) {
		t.Fatal("recovered slot released")
	}
	pending, err := j.CleanupPending()
	if err != nil || len(pending) != 1 || pending[0].Namespace != lease.Namespace {
		t.Fatal("lost cleanup", err)
	}
	if err := j.ConfirmCleanup(Cleanup{lease.ID, "cka-wrong"}); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign namespace acknowledged")
	}
	if err := j.ConfirmCleanup(pending[0]); err != nil {
		t.Fatal(err)
	}
	j.Close()
	j = openTestJournal(t, directory, 1)
	if _, err := j.Start(alice, curated(t)); err != nil {
		t.Fatal("durable cleanup did not release slot", err)
	}
}

func TestJournalExclusiveWriterAndExpiredLeaseCleanup(t *testing.T) {
	directory := journalDir(t)
	j := openTestJournal(t, directory, 1)
	if other, err := OpenJournal(directory, 1); err == nil {
		other.Close()
		t.Fatal("second writer admitted")
	}
	lease, err := j.Start(alice, curated(t))
	if err != nil {
		t.Fatal(err)
	}
	if err := j.ConfirmCleanup(Cleanup{lease.ID, lease.Namespace}); !errors.Is(err, ErrDenied) {
		t.Fatal("active cleanup accepted")
	}
	j.store.now = func() time.Time { return lease.ExpiresAt }
	if _, err := j.ActiveScenario(alice, lease.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("expired operation accepted")
	}
	pending, err := j.CleanupPending()
	if err != nil || len(pending) != 1 {
		t.Fatal("expiry cleanup missing", err)
	}
	j.Close()
	j = openTestJournal(t, directory, 1)
	if _, err := j.Start(bob, curated(t)); !errors.Is(err, ErrCapacity) {
		t.Fatal("expired reservation disappeared on restart")
	}
}

func obstructJournal(t *testing.T, directory string) func() {
	t.Helper()
	path := filepath.Join(directory, journalName)
	backup := filepath.Join(directory, "prior.json")
	if err := os.Rename(path, backup); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	return func() {
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(backup, path); err != nil {
			t.Fatal(err)
		}
	}
}

func TestJournalFailedReservationNeverReturnsALeaseAndPoisonsService(t *testing.T) {
	directory := journalDir(t)
	j := openTestJournal(t, directory, 1)
	repair := obstructJournal(t, directory)
	lease, err := j.Start(alice, curated(t))
	if !errors.Is(err, ErrJournal) || lease.ID != "" {
		t.Fatal("unpersisted lease returned", err)
	}
	if _, err := j.Status(alice, "anything"); !errors.Is(err, ErrJournal) {
		t.Fatal("storage failure ignored")
	}
	if _, err := j.CleanupPending(); !errors.Is(err, ErrJournal) {
		t.Fatal("storage failure ignored by reaper")
	}
	repair()
	if _, err := j.Start(alice, curated(t)); !errors.Is(err, ErrJournal) {
		t.Fatal("poisoned controller resumed without recovery")
	}
	j.Close()
	j = openTestJournal(t, directory, 1)
	if _, err := j.Start(alice, curated(t)); err != nil {
		t.Fatal(err)
	}
}

func TestJournalFailedCleanupAcknowledgmentRetainsDiskObligation(t *testing.T) {
	directory := journalDir(t)
	j := openTestJournal(t, directory, 1)
	lease, err := j.Start(alice, curated(t))
	if err != nil {
		t.Fatal(err)
	}
	clean, err := j.Finish(alice, lease.ID)
	if err != nil {
		t.Fatal(err)
	}
	repair := obstructJournal(t, directory)
	if err := j.ConfirmCleanup(clean); !errors.Is(err, ErrJournal) {
		t.Fatal("failed cleanup save accepted")
	}
	repair()
	j.Close()
	j = openTestJournal(t, directory, 1)
	pending, err := j.CleanupPending()
	if err != nil || len(pending) != 1 || pending[0] != clean {
		t.Fatal("cleanup obligation lost", err)
	}
}

func TestJournalRejectsMissingCorruptAndUnsafeState(t *testing.T) {
	for _, bad := range []string{"", `{`, `{"version":1,"entries":[]}{}`,
		`{"version":1,"version":1,"entries":[]}`, `{"version":1,"entries":[],"extra":true}`,
		`{"version":2,"entries":[]}`, `{"version":1,"entries":null}`} {
		t.Run(bad, func(t *testing.T) {
			directory := journalDir(t)
			j := openTestJournal(t, directory, 1)
			j.Close()
			if err := os.WriteFile(filepath.Join(directory, journalName), []byte(bad), 0600); err != nil {
				t.Fatal(err)
			}
			if other, err := OpenJournal(directory, 1); err == nil {
				other.Close()
				t.Fatal("corrupt state silently reset")
			}
		})
	}
	for _, kind := range []string{"missing", "symlink", "world-readable", "hardlink", "oversize"} {
		t.Run(kind, func(t *testing.T) {
			directory := journalDir(t)
			j := openTestJournal(t, directory, 1)
			j.Close()
			path := filepath.Join(directory, journalName)
			var err error
			switch kind {
			case "missing":
				err = os.Remove(path)
			case "symlink":
				err = os.Remove(path)
				if err == nil {
					err = os.Symlink(filepath.Join(t.TempDir(), "outside"), path)
				}
			case "world-readable":
				err = os.Chmod(path, 0644)
			case "hardlink":
				err = os.Link(path, filepath.Join(directory, "another-link"))
			case "oversize":
				err = os.WriteFile(path, []byte(strings.Repeat(" ", maxJournalBytes+1)), 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if other, err := OpenJournal(directory, 1); err == nil {
				other.Close()
				t.Fatal("unsafe state accepted")
			}
		})
	}
}

func TestJournalRejectsInvalidAndDuplicateLeaseRecords(t *testing.T) {
	for _, kind := range []string{"owner", "id", "namespace", "scenario", "deadline", "phase", "duplicate"} {
		t.Run(kind, func(t *testing.T) {
			directory := journalDir(t)
			j := openTestJournal(t, directory, 2)
			if _, err := j.Start(alice, curated(t)); err != nil {
				t.Fatal(err)
			}
			j.Close()
			path := filepath.Join(directory, journalName)
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var disk diskJournal
			if err := json.Unmarshal(data, &disk); err != nil {
				t.Fatal(err)
			}
			row := &disk.Entries[0]
			switch kind {
			case "owner":
				row.Owner = "evilsysadmin"
			case "id":
				row.ID = "not-random"
			case "namespace":
				row.Scenario.Namespace = "default"
			case "scenario":
				row.Scenario.Minutes = 999
			case "deadline":
				row.Deadline = row.Deadline.Add(time.Second)
			case "phase":
				row.Phase = "prepared"
			case "duplicate":
				disk.Entries = append(disk.Entries, *row)
			}
			data, err = json.Marshal(disk)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			if other, err := OpenJournal(directory, 2); err == nil {
				other.Close()
				t.Fatal("invalid record accepted")
			}
		})
	}
}

func TestJournalConcurrentStartsHaveOneDurableWinner(t *testing.T) {
	directory := journalDir(t)
	j := openTestJournal(t, directory, 1)
	base := curated(t)
	var wg sync.WaitGroup
	results := make(chan error, 24)
	for range 24 {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := j.Start(alice, base); results <- err }()
	}
	wg.Wait()
	close(results)
	winners := 0
	for err := range results {
		if err == nil {
			winners++
		} else if !errors.Is(err, ErrConflict) {
			t.Fatal(err)
		}
	}
	if winners != 1 {
		t.Fatalf("got %d winners", winners)
	}
	j.Close()
	j = openTestJournal(t, directory, 1)
	pending, err := j.CleanupPending()
	if err != nil || len(pending) != 1 {
		t.Fatal("winning reservation was not durable", err)
	}
}

func TestJournalAbruptProcessExitRetainsReservation(t *testing.T) {
	if directory := os.Getenv("CKA_JOURNAL_CRASH_TEST_DIRECTORY"); directory != "" {
		j, err := OpenJournal(directory, 1)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := j.Start(alice, curated(t)); err != nil {
			t.Fatal(err)
		}
		os.Exit(0) // skip Close and all deferred cleanup, as on a controller crash
	}
	directory := journalDir(t)
	command := exec.Command(os.Args[0], "-test.run=^TestJournalAbruptProcessExitRetainsReservation$")
	command.Env = append(os.Environ(), "CKA_JOURNAL_CRASH_TEST_DIRECTORY="+directory)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("crash helper: %v %s", err, output)
	}
	j := openTestJournal(t, directory, 1)
	pending, err := j.CleanupPending()
	if err != nil || len(pending) != 1 {
		t.Fatal("crash lost reservation", err)
	}
	if _, err := j.Start(alice, curated(t)); !errors.Is(err, ErrConflict) {
		t.Fatal("crash released owner")
	}
}

func TestJournalRejectsUnsafeDirectoryAndInvalidCapacity(t *testing.T) {
	directory := journalDir(t)
	for _, limit := range []int{0, -1, maxEntries + 1} {
		if j, err := OpenJournal(directory, limit); err == nil {
			j.Close()
			t.Fatal("invalid capacity accepted")
		}
	}
	if j, err := OpenJournal("relative", 1); err == nil {
		j.Close()
		t.Fatal("relative path accepted")
	}
	if err := os.Chmod(directory, 0755); err != nil {
		t.Fatal(err)
	}
	if j, err := OpenJournal(directory, 1); err == nil {
		j.Close()
		t.Fatal("nonprivate directory accepted")
	}
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "linked")
	if err := os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	if j, err := OpenJournal(link, 1); err == nil {
		j.Close()
		t.Fatal("symlink directory accepted")
	}
	j := openTestJournal(t, directory, 1)
	j.Close()
	if _, err := j.Start(alice, curated(t)); !errors.Is(err, ErrJournal) {
		t.Fatal("closed journal admitted a lease")
	}
}
