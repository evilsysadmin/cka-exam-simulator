//go:build linux

package main

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/examsession"
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

const owner = "11111111-1111-4111-8111-111111111111"
const foreign = "22222222-2222-4222-8222-222222222222"

func fixture(t *testing.T) (*examsession.Journal, map[string]scenario.Scenario, string) {
	t.Helper()
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	j, err := examsession.OpenJournal(directory, 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	bank, err := scenario.LoadBank("../../../scenarios")
	if err != nil {
		t.Fatal(err)
	}
	return j, bank, directory
}

func call(t *testing.T, j *examsession.Journal, bank map[string]scenario.Scenario, fields map[string]string) response {
	t.Helper()
	raw, _ := json.Marshal(fields)
	var output bytes.Buffer
	if err := serve(strings.NewReader(string(raw)+"\n"), &output, j, bank); err != nil {
		t.Fatal(err)
	}
	var answer response
	if json.Unmarshal(output.Bytes(), &answer) != nil {
		t.Fatal("controller emitted invalid JSON")
	}
	return answer
}

func TestOwnerBoundLeaseSurvivesPipeCallsAndRequiresVerifiedCleanup(t *testing.T) {
	j, bank, _ := fixture(t)
	started := call(t, j, bank, map[string]string{"op": "start", "owner": owner, "slug": "scale-payment-api"})
	if !started.OK {
		t.Fatal(started.Error)
	}
	lease := started.Result.(map[string]any)
	id, namespace := lease["ID"].(string), lease["Namespace"].(string)
	status := call(t, j, bank, map[string]string{"op": "status", "owner": owner, "id": id})
	if !status.OK || status.Result.(map[string]any)["phase"] != "active" {
		t.Fatal("lease did not survive another controller request")
	}
	for _, who := range []string{owner, foreign} {
		again := call(t, j, bank, map[string]string{"op": "start", "owner": who, "slug": "scale-payment-api"})
		if again.OK {
			t.Fatal("capacity-one controller admitted a second lease")
		}
	}
	for _, op := range []string{"status", "scenario", "finish"} {
		denied := call(t, j, bank, map[string]string{"op": op, "owner": foreign, "id": id})
		if denied.OK || denied.Error != "denied" {
			t.Fatal("foreign owner obtained lease authority")
		}
	}
	confirmed := call(t, j, bank, map[string]string{"op": "confirm", "id": id, "namespace": namespace})
	if confirmed.OK {
		t.Fatal("active lease released without closing")
	}
	closed := call(t, j, bank, map[string]string{"op": "finish", "owner": owner, "id": id})
	if !closed.OK {
		t.Fatal(closed.Error)
	}
	if answer := call(t, j, bank, map[string]string{"op": "confirm", "id": id, "namespace": "cka-lab-incorrect"}); answer.OK {
		t.Fatal("mismatched cleanup released capacity")
	}
	if answer := call(t, j, bank, map[string]string{"op": "confirm", "id": id, "namespace": namespace}); !answer.OK {
		t.Fatal("exact verified cleanup denied")
	}
	if answer := call(t, j, bank, map[string]string{"op": "start", "owner": foreign, "slug": "scale-payment-api"}); !answer.OK {
		t.Fatal("confirmed cleanup did not release capacity")
	}
}

func TestRestartClosesPriorLeaseAndRetainsCleanupReservation(t *testing.T) {
	j, bank, directory := fixture(t)
	started := call(t, j, bank, map[string]string{"op": "start", "owner": owner, "slug": "scale-payment-api"})
	if !started.OK {
		t.Fatal(started.Error)
	}
	lease := started.Result.(map[string]any)
	j.Close()
	recovered, err := examsession.OpenJournal(directory, 1)
	if err != nil {
		t.Fatal(err)
	}
	defer recovered.Close()
	status := call(t, recovered, bank, map[string]string{"op": "status", "owner": owner, "id": lease["ID"].(string)})
	if !status.OK || status.Result.(map[string]any)["phase"] != "closed" {
		t.Fatal("restart revived active lease")
	}
	pending := call(t, recovered, bank, map[string]string{"op": "pending"})
	if !pending.OK || len(pending.Result.([]any)) != 1 {
		t.Fatal("restart lost cleanup obligation")
	}
	if answer := call(t, recovered, bank, map[string]string{"op": "start", "owner": foreign, "slug": "scale-payment-api"}); answer.OK {
		t.Fatal("restart released occupied capacity")
	}
}

func TestMalformedCommandsNeverChangeJournal(t *testing.T) {
	j, bank, _ := fixture(t)
	for _, line := range []string{
		`{"op":"start","op":"pending","owner":"` + owner + `","slug":"scale-payment-api"}`,
		`{"op":"pending","owner":null}`, `{"op":"pending","extra":"private"}`,
		`{"op":"pending"} {"op":"pending"}`, `{"op":"pending","nested":{}}`,
		`{"op":"pending","array":[]}`, `[]`, `{"op":"restore"}`, `{"op":true}`,
	} {
		var output bytes.Buffer
		if err := serve(strings.NewReader(line+"\n"), &output, j, bank); err != nil {
			t.Fatal(err)
		}
		var answer response
		if json.Unmarshal(output.Bytes(), &answer) != nil || answer.OK || answer.Error != "invalid" || answer.Result != nil {
			t.Fatal("malformed request accepted or private error disclosed", output.String())
		}
	}
	if answer := call(t, j, bank, map[string]string{"op": "start", "owner": owner, "slug": "scale-payment-api"}); !answer.OK {
		t.Fatal("malformed commands mutated the journal")
	}
}

func TestOversizedInputAndBrokenJournalStopController(t *testing.T) {
	j, bank, _ := fixture(t)
	var output bytes.Buffer
	if serve(strings.NewReader(strings.Repeat("x", 8193)+"\n"), &output, j, bank) == nil || output.Len() != 0 {
		t.Fatal("oversized input was processed")
	}
	j.Close()
	input := strings.NewReader("{\"op\":\"pending\"}\n{\"op\":\"pending\"}\n")
	if serve(input, &output, j, bank) == nil || strings.Count(output.String(), "\n") != 1 || !strings.Contains(output.String(), "journal-unavailable") {
		t.Fatal("unavailable journal continued serving")
	}
}
