// cka-candidate-views produces only the public projection of trusted scenarios.
// It never starts a server or exposes private fixture or grading data.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

func main() {
	bankPath := flag.String("bank", "../scenarios", "path to trusted curated JSON bank")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(2)
	}
	bank, err := scenario.LoadBank(*bankPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "CKA_GO_CANDIDATE_INVALID:", err)
		os.Exit(1)
	}
	views, err := scenario.CandidateBank(bank)
	if err != nil {
		fmt.Fprintln(os.Stderr, "CKA_GO_CANDIDATE_INVALID:", err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(views); err != nil {
		fmt.Fprintln(os.Stderr, "CKA_GO_CANDIDATE_ENCODING_FAILED")
		os.Exit(1)
	}
}
