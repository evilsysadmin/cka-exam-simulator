//go:build linux

// cka-lease-stdio exposes the durable capacity-one lease journal to a trusted
// local controller over its private stdin/stdout pipes. It has no network
// listener, Kubernetes access or authentication endpoint. Owner UUIDs must be
// supplied only after the controller's fresh Identity authorization decision.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/examsession"
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

type response struct {
	OK     bool   `json:"ok"`
	Error  string `json:"error,omitempty"`
	Result any    `json:"result,omitempty"`
}

// Requests are flat string-only JSON objects. Reject duplicates, nulls, nested
// values, unknown fields and trailing documents before touching the journal.
func request(data []byte) (map[string]string, error) {
	d := json.NewDecoder(bytes.NewReader(data))
	start, err := d.Token()
	if err != nil || start != json.Delim('{') {
		return nil, examsession.ErrInvalid
	}
	fields := make(map[string]string)
	for d.More() {
		key, err := d.Token()
		name, ok := key.(string)
		if err != nil || !ok || len(fields) >= 6 {
			return nil, examsession.ErrInvalid
		}
		if _, exists := fields[name]; exists {
			return nil, examsession.ErrInvalid
		}
		value, err := d.Token()
		text, ok := value.(string)
		if err != nil || !ok || text == "" || len(text) > 128 {
			return nil, examsession.ErrInvalid
		}
		fields[name] = text
	}
	end, err := d.Token()
	if err != nil || end != json.Delim('}') {
		return nil, examsession.ErrInvalid
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, examsession.ErrInvalid
	}
	return fields, nil
}

func exact(fields map[string]string, required ...string) bool {
	if len(fields) != len(required)+1 || fields["op"] == "" {
		return false
	}
	for _, name := range required {
		if fields[name] == "" {
			return false
		}
	}
	return true
}

func dispatch(j *examsession.Journal, bank map[string]scenario.Scenario, fields map[string]string) (any, error) {
	switch fields["op"] {
	case "start":
		if !exact(fields, "owner", "slug") {
			break
		}
		base, ok := bank[fields["slug"]]
		if !ok {
			break
		}
		return j.Start(fields["owner"], base)
	case "status":
		if exact(fields, "owner", "id") {
			return j.Status(fields["owner"], fields["id"])
		}
	case "scenario":
		if exact(fields, "owner", "id") {
			return j.ActiveScenario(fields["owner"], fields["id"])
		}
	case "finish":
		if exact(fields, "owner", "id") {
			return j.Finish(fields["owner"], fields["id"])
		}
	case "pending":
		if exact(fields) {
			pending, err := j.CleanupPending()
			if pending == nil {
				pending = []examsession.Cleanup{}
			}
			return pending, err
		}
	case "confirm":
		if exact(fields, "id", "namespace") {
			// Trusted controller only: Kubernetes deletion must already have
			// been verified. This pipe must never be a candidate-facing API.
			return true, j.ConfirmCleanup(examsession.Cleanup{ID: fields["id"], Namespace: fields["namespace"]})
		}
	}
	return nil, examsession.ErrInvalid
}

func errorCode(err error) string {
	switch {
	case errors.Is(err, examsession.ErrDenied):
		return "denied"
	case errors.Is(err, examsession.ErrConflict):
		return "conflict"
	case errors.Is(err, examsession.ErrCapacity):
		return "capacity"
	case errors.Is(err, examsession.ErrJournal):
		return "journal-unavailable"
	default:
		return "invalid"
	}
}

func serve(input io.Reader, output io.Writer, j *examsession.Journal, bank map[string]scenario.Scenario) error {
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 1024), 8192)
	encoder := json.NewEncoder(output)
	for scanner.Scan() {
		fields, err := request(scanner.Bytes())
		var result any
		if err == nil {
			result, err = dispatch(j, bank, fields)
		}
		answer := response{OK: err == nil, Result: result}
		if err != nil {
			answer.Error, answer.Result = errorCode(err), nil
		}
		if writeErr := encoder.Encode(answer); writeErr != nil {
			return writeErr
		}
		if errors.Is(err, examsession.ErrJournal) {
			return err // a poisoned journal cannot authorize another operation
		}
	}
	return scanner.Err()
}

func run() error {
	directory := flag.String("journal", "", "private operator-created journal directory")
	bankPath := flag.String("bank", "", "absolute trusted curated scenario directory")
	flag.Parse()
	if flag.NArg() != 0 || !filepath.IsAbs(*bankPath) {
		return examsession.ErrInvalid
	}
	bank, err := scenario.LoadBank(*bankPath)
	if err != nil {
		return err
	}
	journal, err := examsession.OpenJournal(*directory, 1)
	if err != nil {
		return err
	}
	defer journal.Close()
	return serve(os.Stdin, os.Stdout, journal, bank)
}

func main() {
	if run() != nil {
		fmt.Fprintln(os.Stderr, "CKA_LEASE_CONTROLLER_UNAVAILABLE")
		os.Exit(1)
	}
}
