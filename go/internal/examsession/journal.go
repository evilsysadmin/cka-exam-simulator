//go:build linux

package examsession

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

var ErrJournal = errors.New("exam journal unavailable; controller recovery required")

const journalName = "leases.json"
const maxJournalBytes = 1 << 20

var leaseID = regexp.MustCompile(`^[a-f0-9]{32}$`)
var leaseNamespace = regexp.MustCompile(`^cka-lab-[a-f0-9]{32}$`)

type diskRecord struct {
	Owner    string            `json:"owner"`
	ID       string            `json:"id"`
	Scenario scenario.Scenario `json:"scenario"`
	Started  time.Time         `json:"started"`
	Deadline time.Time         `json:"deadline"`
	Phase    Phase             `json:"phase"`
}

type diskJournal struct {
	Version int          `json:"version"`
	Entries []diskRecord `json:"entries"`
}

// Journal durably reserves leases before they can be returned for provisioning.
// This Linux-only controller primitive is NOT an authentication boundary or a
// Kubernetes controller. Use an operator-created local, private 0700 directory
// on a filesystem supporting flock, atomic rename and fsync (not NFS).
//
// All recovered leases become closed cleanup obligations, even if unexpired.
// The controller must verify deletion before ConfirmCleanup. A storage error
// poisons the instance: no further read or write can authorize lab operations.
type Journal struct {
	mu        sync.Mutex
	store     *Store
	directory string
	lock      *os.File
	failed    bool
	closed    bool
}

// OpenJournal requires an explicit capacity (1 for the initial staging pilot).
// A missing/corrupt journal beside an existing lock is a recovery error, never
// an empty store. Operators must reconcile orphan resources before repairing it.
func OpenJournal(directory string, limit int) (*Journal, error) {
	if !filepath.IsAbs(directory) || limit < 1 || limit > maxEntries {
		return nil, ErrJournal
	}
	clean := filepath.Clean(directory)
	resolved, err := filepath.EvalSymlinks(clean)
	if err != nil || resolved != clean {
		return nil, ErrJournal
	}
	info, err := os.Lstat(clean)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return nil, ErrJournal
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return nil, ErrJournal
	}
	lockPath := filepath.Join(clean, "journal.lock")
	flags := syscall.O_RDWR | syscall.O_CLOEXEC | syscall.O_NOFOLLOW
	fd, err := syscall.Open(lockPath, flags|syscall.O_CREAT|syscall.O_EXCL, 0600)
	created := err == nil
	if errors.Is(err, syscall.EEXIST) {
		fd, err = syscall.Open(lockPath, flags, 0)
	}
	if err != nil {
		return nil, ErrJournal
	}
	lock := os.NewFile(uintptr(fd), lockPath)
	j := &Journal{directory: clean, lock: lock, store: newStore(time.Now, nil, limit)}
	fail := func() (*Journal, error) { lock.Close(); return nil, ErrJournal }
	lockInfo, err := lock.Stat()
	if err != nil || !privateRegular(lockInfo) || syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return fail()
	}
	data, err := readPrivateJournal(filepath.Join(clean, journalName))
	if created {
		if !errors.Is(err, os.ErrNotExist) {
			return fail()
		}
	} else if err != nil || j.restore(data) != nil {
		return fail()
	}
	// Reuse the memory implementation's CSPRNG, independent namespace allocation,
	// ownership checks and capacity rules. Persist recovery before admission.
	j.store.random = rand.Reader
	if j.persist() != nil {
		return fail()
	}
	return j, nil
}

func privateRegular(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && info.Mode().IsRegular() && info.Mode().Perm() == 0600 &&
		stat.Uid == uint32(os.Geteuid()) && stat.Nlink == 1
}

func readPrivateJournal(path string) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !privateRegular(info) || info.Size() > maxJournalBytes {
		return nil, ErrJournal
	}
	return io.ReadAll(io.LimitReader(f, maxJournalBytes+1))
}

func (j *Journal) restore(data []byte) error {
	if len(data) > maxJournalBytes || !utf8.Valid(data) {
		return ErrJournal
	}
	// Reject duplicate keys and trailing values before strict typed decoding.
	check := json.NewDecoder(bytes.NewReader(data))
	if uniqueJSON(check, 0) != nil {
		return ErrJournal
	}
	if _, err := check.Token(); err != io.EOF {
		return ErrJournal
	}
	var disk diskJournal
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&disk) != nil || disk.Version != 1 || disk.Entries == nil || len(disk.Entries) > j.store.limit {
		return ErrJournal
	}
	namespaces := make(map[string]bool)
	for _, row := range disk.Entries {
		if !ownerUUID.MatchString(row.Owner) || !leaseID.MatchString(row.ID) ||
			!leaseNamespace.MatchString(row.Scenario.Namespace) || row.Scenario.Validate() != nil ||
			row.Started.IsZero() || !row.Deadline.Equal(row.Started.Add(time.Duration(row.Scenario.Minutes)*time.Minute)) ||
			(row.Phase != Active && row.Phase != Expired && row.Phase != Closed) ||
			j.store.entries[row.ID] != nil || j.store.owners[row.Owner] != "" || namespaces[row.Scenario.Namespace] {
			return ErrJournal
		}
		namespaces[row.Scenario.Namespace] = true
		j.store.entries[row.ID] = &record{owner: row.Owner, id: row.ID, scenario: row.Scenario,
			started: row.Started, deadline: row.Deadline, phase: Closed}
		j.store.owners[row.Owner] = row.ID
	}
	return nil
}

func uniqueJSON(decoder *json.Decoder, depth int) error {
	if depth > 16 {
		return ErrJournal
	}
	token, err := decoder.Token()
	if err != nil {
		return ErrJournal
	}
	delim, compound := token.(json.Delim)
	if !compound {
		return nil
	}
	if delim != '{' && delim != '[' {
		return ErrJournal
	}
	keys := make(map[string]bool)
	for decoder.More() {
		if delim == '{' {
			key, err := decoder.Token()
			name, ok := key.(string)
			if err != nil || !ok || keys[name] {
				return ErrJournal
			}
			keys[name] = true
		}
		if uniqueJSON(decoder, depth+1) != nil {
			return ErrJournal
		}
	}
	end, err := decoder.Token()
	if err != nil || (delim == '{' && end != json.Delim('}')) || (delim == '[' && end != json.Delim(']')) {
		return ErrJournal
	}
	return nil
}

// persist is called only with the Journal mutex held (or during construction).
// Atomic replacement plus directory fsync fences the successful return from
// Start and ConfirmCleanup. A failed write never grants a returned lease.
func (j *Journal) persist() (err error) {
	defer func() {
		if err != nil {
			j.failed = true
		}
	}()
	disk := diskJournal{Version: 1, Entries: make([]diskRecord, 0, len(j.store.entries))}
	for _, r := range j.store.entries {
		disk.Entries = append(disk.Entries, diskRecord{r.owner, r.id, r.scenario, r.started.UTC(), r.deadline.UTC(), r.phase})
	}
	data, err := json.Marshal(disk)
	if err != nil || len(data) > maxJournalBytes {
		return ErrJournal
	}
	f, err := os.CreateTemp(j.directory, ".leases-")
	if err != nil {
		return ErrJournal
	}
	defer func() { f.Close(); os.Remove(f.Name()) }()
	if _, err = f.Write(data); err != nil {
		return ErrJournal
	}
	if err = f.Sync(); err != nil {
		return ErrJournal
	}
	if err = f.Close(); err != nil {
		return ErrJournal
	}
	if err = os.Rename(f.Name(), filepath.Join(j.directory, journalName)); err != nil {
		return ErrJournal
	}
	directory, err := os.Open(j.directory)
	if err != nil {
		return ErrJournal
	}
	defer directory.Close()
	if directory.Sync() != nil {
		return ErrJournal
	}
	return nil
}

func (j *Journal) ready() bool { return j != nil && !j.failed && !j.closed }

func (j *Journal) Start(owner string, base scenario.Scenario) (Lease, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return Lease{}, ErrJournal
	}
	lease, err := j.store.Start(owner, base)
	if err != nil {
		return Lease{}, err
	}
	if j.persist() != nil {
		return Lease{}, ErrJournal
	}
	return lease, nil
}

func (j *Journal) Status(owner, id string) (View, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return View{}, ErrJournal
	}
	return j.store.Status(owner, id)
}

func (j *Journal) ActiveScenario(owner, id string) (scenario.Scenario, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return scenario.Scenario{}, ErrJournal
	}
	return j.store.ActiveScenario(owner, id)
}

func (j *Journal) Finish(owner, id string) (Cleanup, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return Cleanup{}, ErrJournal
	}
	clean, err := j.store.Finish(owner, id)
	if err != nil {
		return Cleanup{}, err
	}
	if j.persist() != nil {
		return Cleanup{}, ErrJournal
	}
	return clean, nil
}

func (j *Journal) CleanupPending() ([]Cleanup, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return nil, ErrJournal
	}
	pending := j.store.CleanupPending()
	if j.persist() != nil {
		return nil, ErrJournal
	}
	return pending, nil
}

func (j *Journal) ConfirmCleanup(clean Cleanup) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.ready() {
		return ErrJournal
	}
	if err := j.store.ConfirmCleanup(clean); err != nil {
		return err
	}
	return j.persist()
}

func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.closed {
		return nil
	}
	j.closed = true
	return j.lock.Close()
}
