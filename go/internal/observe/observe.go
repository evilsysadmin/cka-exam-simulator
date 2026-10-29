package observe

import (
	"context"
	"encoding/json"
	"errors"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/grading"
	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

var (
	deployments = schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}
	services    = schema.GroupVersionResource{Version: "v1", Resource: "services"}
	slices      = schema.GroupVersionResource{Group: "discovery.k8s.io", Version: "v1", Resource: "endpointslices"}
)

// Reader deliberately offers ONLY GET and LIST. The production adapter never
// exposes create, patch, update, delete, exec, watch or impersonation.
type Reader interface {
	Get(context.Context, schema.GroupVersionResource, string, string) (map[string]any, error)
	List(context.Context, schema.GroupVersionResource, string, string) (map[string]any, error)
}

type dynamicReader struct{ client dynamic.Interface }

// NewReader wraps a client-go dynamic client in a restricted read-only API.
func NewReader(client dynamic.Interface) Reader {
	return dynamicReader{client: client}
}

// normalize maps unstructured integer fields to JSON float64, exactly as
// Python's json.loads(kubectl -o json) and the existing Go grader expect.
func normalize(value any) (map[string]any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, errors.New("cannot encode Kubernetes object snapshot")
	}
	var result map[string]any
	if err := json.Unmarshal(raw, &result); err != nil || result == nil {
		return nil, errors.New("cannot decode Kubernetes object snapshot")
	}
	return result, nil
}

func (r dynamicReader) Get(ctx context.Context, resource schema.GroupVersionResource,
	namespace, name string) (map[string]any, error) {
	object, err := r.client.Resource(resource).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	return normalize(object.Object)
}

func (r dynamicReader) List(ctx context.Context, resource schema.GroupVersionResource,
	namespace, labelSelector string) (map[string]any, error) {
	list, err := r.client.Resource(resource).Namespace(namespace).List(ctx, metav1.ListOptions{
		LabelSelector: labelSelector,
		Limit:         0, // no pagination: current test lab has one Service's EndpointSlices
	})
	if err != nil {
		return nil, err
	}
	if list.GetContinue() != "" {
		return nil, errors.New("EndpointSlice list was truncated")
	}
	items := make([]any, 0, len(list.Items))
	for _, item := range list.Items {
		items = append(items, item.Object)
	}
	return normalize(map[string]any{"items": items})
}

type Snapshot struct {
	Deployment     map[string]any
	Service        map[string]any
	EndpointSlices map[string]any
}

func getOrMissing(ctx context.Context, reader Reader, resource schema.GroupVersionResource,
	namespace, name string) (map[string]any, error) {
	found, err := reader.Get(ctx, resource, namespace, name)
	if apierrors.IsNotFound(err) {
		// Same semantics as Python's _get_json: a missing workload is a
		// failed solution, not a 500 or an imaginary successful result.
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, errors.New("Kubernetes observation failed")
	}
	if found == nil {
		return nil, errors.New("Kubernetes observer returned a missing object")
	}
	return found, nil
}

// ReadSnapshots only queries exactly the three resource types and the
// scenario's validated namespace. The caller cannot provide an arbitrary GVR.
func ReadSnapshots(ctx context.Context, s scenario.Scenario, reader Reader) (Snapshot, error) {
	if err := s.Validate(); err != nil {
		return Snapshot{}, errors.New("invalid curated scenario")
	}
	if reader == nil {
		return Snapshot{}, errors.New("no Kubernetes reader configured")
	}
	deployment, err := getOrMissing(ctx, reader, deployments, s.Namespace, s.Deployment)
	if err != nil {
		return Snapshot{}, err
	}
	result := Snapshot{Deployment: deployment}
	if s.ScenarioType != scenario.ServiceSelector {
		return result, nil
	}
	serviceName := s.Deployment + "-svc"
	service, err := getOrMissing(ctx, reader, services, s.Namespace, serviceName)
	if err != nil {
		return Snapshot{}, err
	}
	list, err := reader.List(ctx, slices, s.Namespace,
		"kubernetes.io/service-name="+serviceName)
	if err != nil {
		return Snapshot{}, errors.New("Kubernetes EndpointSlice observation failed")
	}
	if list == nil {
		return Snapshot{}, errors.New("Kubernetes observer returned no EndpointSlice list")
	}
	result.Service = service
	result.EndpointSlices = list
	return result, nil
}

// Grade runs the already parity-verified deterministic Go grader over actual
// read-only snapshots. No LLM, host shell or cluster mutations are involved.
func Grade(ctx context.Context, s scenario.Scenario, reader Reader) (grading.Grade, error) {
	snapshots, err := ReadSnapshots(ctx, s, reader)
	if err != nil {
		return grading.Grade{}, err
	}
	return grading.State(s, snapshots.Deployment, snapshots.Service, snapshots.EndpointSlices), nil
}

