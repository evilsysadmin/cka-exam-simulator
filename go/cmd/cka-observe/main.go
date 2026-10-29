// cka-observe is a manual, read-only local kind observer for Go migration CI.
// It is NOT an exam API, runner, terminal or production service.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"k8s.io/client-go/dynamic"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/observe"
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

func main() {
	live := flag.Bool("dev-live", false, "explicitly allow local kind read-only observation")
	bankPath := flag.String("bank", "../scenarios", "trusted curated bank path")
	kubeconfig := flag.String("kubeconfig", "", "absolute dedicated kind kubeconfig")
	slug := flag.String("slug", "", "curated scenario slug")
	flag.Parse()
	if flag.NArg() != 0 || !*live || *kubeconfig == "" || *slug == "" {
		fmt.Fprintln(os.Stderr, "CKA_GO_OBSERVER_DISABLED: requires -dev-live, -slug, -kubeconfig and no other args")
		os.Exit(2)
	}
	bank, err := scenario.LoadBank(*bankPath)
	if err != nil {
		fail()
	}
	s, ok := bank[*slug]
	if !ok {
		fail()
	}
	cfg, err := observe.LoadScopedLocalConfig(*kubeconfig, s.Namespace)
	if err != nil {
		fail()
	}
	client, err := dynamic.NewForConfig(cfg)
	if err != nil {
		fail()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	grade, err := observe.Grade(ctx, s, observe.NewReader(client))
	if err != nil {
		fail()
	}
	if err := json.NewEncoder(os.Stdout).Encode(grade); err != nil {
		fail()
	}
}

func fail() {
	// Never print raw Kubernetes or TLS error text, auth details or kubeconfig.
	fmt.Fprintln(os.Stderr, "CKA_GO_OBSERVER_FAILED: refused or unavailable dedicated local kind read")
	os.Exit(1)
}
