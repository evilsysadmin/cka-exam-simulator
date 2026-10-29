// cka-scenarios validates the trusted curated scenario bank offline.
// It never starts a Kubernetes cluster or publishes model output.
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

func main() {
	bankPath := flag.String("bank", "scenarios", "directory containing curated scenario JSON")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(2)
	}
	bank, err := scenario.LoadBank(*bankPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "CKA_GO_BANK_INVALID:", err)
		os.Exit(1)
	}
	fmt.Printf("CKA_GO_BANK_VALID scenarios=%d\n", len(bank))
}
