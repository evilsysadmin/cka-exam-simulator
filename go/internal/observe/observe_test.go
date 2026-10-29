package observe

import (
	"context"
	"encoding/base64"
	"fmt"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/tools/clientcmd/api"

	"github.com/evilsysadmin/cka-exam-simulator/go/internal/scenario"
)

func trustedConfig() *api.Config {
	return &api.Config{
		CurrentContext: Context,
		Contexts: map[string]*api.Context{
			Context: {Cluster: Context, AuthInfo: Context},
		},
		Clusters: map[string]*api.Cluster{
			Context: {Server: "https://127.0.0.1:39487", CertificateAuthorityData: []byte("fake-ca")},
		},
		AuthInfos: map[string]*api.AuthInfo{
			Context: {ClientCertificateData: []byte("fake-cert"), ClientKeyData: []byte("fake-key")},
		},
	}
}

func TestRejectUnsafeKubeconfigsBeforeClientCreation(t *testing.T) {
	cases := map[string]func(*api.Config){
		"wrong context": func(c *api.Config) { c.CurrentContext = "production" },
		"additional context": func(c *api.Config) { c.Contexts["production"] = &api.Context{} },
		"changed cluster": func(c *api.Config) { c.Contexts[Context].Cluster = "prod" },
		"namespace override": func(c *api.Config) { c.Contexts[Context].Namespace = "kube-system" },
		"remote API": func(c *api.Config) { c.Clusters[Context].Server = "https://203.0.113.12:6443" },
		"DNS API": func(c *api.Config) { c.Clusters[Context].Server = "https://localhost:6443" },
		"plain HTTP": func(c *api.Config) { c.Clusters[Context].Server = "http://127.0.0.1:8080" },
		"missing port": func(c *api.Config) { c.Clusters[Context].Server = "https://127.0.0.1" },
		"proxy API": func(c *api.Config) { c.Clusters[Context].ProxyURL = "https://evil.example" },
		"skip TLS": func(c *api.Config) { c.Clusters[Context].InsecureSkipTLSVerify = true },
		"external CA file": func(c *api.Config) { c.Clusters[Context].CertificateAuthority = "/tmp/ca.pem" },
		"missing CA": func(c *api.Config) { c.Clusters[Context].CertificateAuthorityData = nil },
		"exec plugin": func(c *api.Config) { c.AuthInfos[Context].Exec = &api.ExecConfig{Command:"sh"} },
		"impersonation": func(c *api.Config) { c.AuthInfos[Context].Impersonate = "system:admin" },
		"token file": func(c *api.Config) { c.AuthInfos[Context].TokenFile = "/tmp/token" },
		"token": func(c *api.Config) { c.AuthInfos[Context].Token = "secret" },
		"external client cert": func(c *api.Config) { c.AuthInfos[Context].ClientCertificate = "/tmp/cert" },
		"external key": func(c *api.Config) { c.AuthInfos[Context].ClientKey = "/tmp/key" },
		"missing client cert": func(c *api.Config) { c.AuthInfos[Context].ClientCertificateData = nil },
		"missing client key": func(c *api.Config) { c.AuthInfos[Context].ClientKeyData = nil },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			c := trustedConfig()
			mutate(c)
			if err := validate(c); err == nil {
				t.Fatal("unsafe Kubernetes context was accepted")
			}
		})
	}
	if err := validate(trustedConfig()); err != nil {
		t.Fatalf("trusted fixed-context config refused: %v", err)
	}
	if err := validate(nil); err == nil { t.Fatal("nil config accepted") }
}

func TestEndpointGuardsRequireLiteralLoopbackIP(t *testing.T) {
	for _, server := range []string{
		"https://127.0.0.1:6443", "https://[::1]:6443",
	} {
		if err := checkedLocalURL(server); err != nil { t.Fatal(err) }
	}
	for _, server := range []string{
		"https://prod.example:6443", "https://127.0.0.2.evil.com:6443",
		"https://8.8.8.8:6443", "http://127.0.0.1:6443",
		"https://127.0.0.1:6443/hidden",
		"https://127.0.0.1:6443?proxy=true",
		"https://user:password@127.0.0.1:6443",
	} {
		if err := checkedLocalURL(server); err == nil {
			t.Errorf("unsafe host accepted: %s", server)
		}
	}
	_, err := url.Parse("https://127.0.0.1:6443")
	if err != nil { t.Fatal(err) }
}

func TestKubeconfigRequiresPrivateAbsoluteNonSymlinkFile(t *testing.T) {
	if _, err := LoadLocalConfig("relative/config"); err == nil { t.Fatal("relative kubeconfig accepted") }
	dir := t.TempDir()
	path := filepath.Join(dir, "kubeconfig")
	if err := os.WriteFile(path, []byte("not-a-kubeconfig"), 0644); err != nil { t.Fatal(err) }
	if _, err := LoadLocalConfig(path); err == nil { t.Fatal("world-readable kubeconfig accepted") }
	if err := os.Chmod(path, 0600); err != nil { t.Fatal(err) }
	link := filepath.Join(dir, "link")
	if err := os.Symlink(path, link); err != nil { t.Fatal(err) }
	if _, err := LoadLocalConfig(link); err == nil { t.Fatal("symlink kubeconfig accepted") }
	if _, err := LoadLocalConfig(path); err == nil { t.Fatal("corrupt kubeconfig accepted") }
}

type request struct {
	verb string
	resource schema.GroupVersionResource
	namespace string
	nameOrSelector string
}
type fakeReader struct {
	calls []request
	get func(schema.GroupVersionResource,string,string)(map[string]any,error)
	list func(schema.GroupVersionResource,string,string)(map[string]any,error)
}
func (f *fakeReader) Get(_ context.Context, r schema.GroupVersionResource, ns, name string)(map[string]any,error) {
	f.calls=append(f.calls,request{"get",r,ns,name})
	if f.get!=nil{return f.get(r,ns,name)}
	return nil,errors.New("unexpected GET")
}
func (f *fakeReader) List(_ context.Context,r schema.GroupVersionResource,ns,label string)(map[string]any,error){
	f.calls=append(f.calls,request{"list",r,ns,label})
	if f.list!=nil{return f.list(r,ns,label)}
	return nil,errors.New("unexpected LIST")
}
func bank(t *testing.T)map[string]scenario.Scenario{
	t.Helper()
	b,err:=scenario.LoadBank("../../../scenarios")
	if err!=nil { t.Fatal(err) }
	return b
}
func TestObserverUsesOnlyReadOperationsAndExactNamespace(t *testing.T){
	b:=bank(t)
	scale:=b["scale-payment-api"]
	f:=&fakeReader{get:func(r schema.GroupVersionResource,ns,name string)(map[string]any,error){
		return map[string]any{"spec":map[string]any{"replicas":float64(3)},"status":map[string]any{"availableReplicas":float64(3)}},nil
	}}
	g,err:=Grade(context.Background(),scale,f)
	if err!=nil || !g.Passed{t.Fatalf("scale snapshot: grade=%+v err=%v",g,err)}
	if len(f.calls)!=1 || f.calls[0]!=(request{"get",deployments,scale.Namespace,scale.Deployment}){
		t.Fatalf("unexpected Kubernetes read: %+v",f.calls)
	}

	svc:=b["fix-catalog-service"]
	f=&fakeReader{
		get:func(r schema.GroupVersionResource,ns,name string)(map[string]any,error){
			switch r {
			case deployments:return map[string]any{"spec":map[string]any{"replicas":float64(2)}},nil
			case services:return map[string]any{"spec":map[string]any{"selector":map[string]any{"app":svc.TargetSelector}}},nil
			default:return nil,errors.New("forbidden GVR")
			}
		},
		list:func(r schema.GroupVersionResource,ns,label string)(map[string]any,error){
			return map[string]any{"items":[]any{map[string]any{"endpoints":[]any{map[string]any{"conditions":map[string]any{"ready":true}}}}}},nil
		},
	}
	g,err=Grade(context.Background(),svc,f)
	if err!=nil || !g.Passed{t.Fatalf("service snapshot: grade=%+v err=%v",g,err)}
	if len(f.calls)!=3 ||
		f.calls[0]!=(request{"get",deployments,svc.Namespace,svc.Deployment}) ||
		f.calls[1]!=(request{"get",services,svc.Namespace,svc.Deployment+"-svc"}) ||
		f.calls[2]!=(request{"list",slices,svc.Namespace,"kubernetes.io/service-name="+svc.Deployment+"-svc"}){
		t.Fatalf("unexpected Kubernetes reads: %+v",f.calls)
	}
}

func TestNotFoundFailsAsUnsolvedButForbiddenOperationsFailClosed(t *testing.T){
	b:=bank(t)
	scale:=b["scale-payment-api"]
	f:=&fakeReader{get:func(r schema.GroupVersionResource,ns,name string)(map[string]any,error){
		return nil,apierrors.NewNotFound(schema.GroupResource{Group:r.Group,Resource:r.Resource},name)
	}}
	g,err:=Grade(context.Background(),scale,f)
	if err!=nil || g.Passed || g.Score!=0{t.Fatalf("missing deployment should fail grading: %+v %v",g,err)}
	f=&fakeReader{get:func(r schema.GroupVersionResource,ns,name string)(map[string]any,error){
		return nil,apierrors.NewForbidden(schema.GroupResource{Group:r.Group,Resource:r.Resource},name,errors.New("denied"))
	}}
	if _,err=Grade(context.Background(),scale,f);err==nil || !strings.Contains(err.Error(),"observation failed"){
		t.Fatalf("forbidden API must fail closed: %v",err)
	}
	f=&fakeReader{}
	bad:=scale;bad.Namespace="kube-system"
	if _,err=Grade(context.Background(),bad,f);err==nil || len(f.calls)!=0{
		t.Fatal("invalid scenario should never reach Kubernetes reader")
	}
}
func TestNormalizeUnstructuredJSONNumbers(t *testing.T){
	got,err:=normalize(map[string]any{"spec":map[string]any{"replicas":int64(3)}})
	if err!=nil { t.Fatal(err) }
	if got["spec"].(map[string]any)["replicas"]!=float64(3){t.Fatalf("unexpected number type: %T",got["spec"].(map[string]any)["replicas"])}
}

func scopedTestConfig(namespace, subject string) *api.Config {
	cfg := trustedConfig()
	claims := base64.RawURLEncoding.EncodeToString([]byte(
		fmt.Sprintf("{%q:%q}", "sub", subject)))
	cfg.Contexts[Context].Namespace = namespace
	cfg.AuthInfos[Context] = &api.AuthInfo{Token: "header." + claims + ".signature"}
	return cfg
}

func TestScopedTokenRequiresExactServiceAccountAndNamespace(t *testing.T) {
	ns := "cka-payments"
	sub := "system:serviceaccount:" + ns + ":" + ObserverAccount
	cases := map[string]func(*api.Config){
		"missing token": func(c *api.Config) { c.AuthInfos[Context].Token = "" },
		"admin token": func(c *api.Config) { c.AuthInfos[Context].Token = "fake.admin.token" },
		"wrong SA": func(c *api.Config) { c.AuthInfos[Context] = scopedTestConfig(ns, "system:serviceaccount:"+ns+":default").AuthInfos[Context] },
		"wrong namespace in token": func(c *api.Config) { c.AuthInfos[Context] = scopedTestConfig(ns, "system:serviceaccount:cka-other:"+ObserverAccount).AuthInfos[Context] },
		"wrong namespace in context": func(c *api.Config) { c.Contexts[Context].Namespace = "cka-other" },
		"embedded admin certificate": func(c *api.Config) { c.AuthInfos[Context].ClientCertificateData = []byte("admin-cert") },
		"token file": func(c *api.Config) { c.AuthInfos[Context].TokenFile = "/tmp/exfil" },
		"exec credential helper": func(c *api.Config) { c.AuthInfos[Context].Exec = &api.ExecConfig{Command: "sh"} },
		"remote API endpoint": func(c *api.Config) { c.Clusters[Context].Server = "https://203.0.113.1:6443" },
		"extra context": func(c *api.Config) { c.Contexts["production"] = &api.Context{} },
	}
	valid := scopedTestConfig(ns, sub)
	if err := validateForNamespace(valid, ns); err != nil { t.Fatalf("scoped token rejected: %v", err) }
	if err := validate(valid); err == nil { t.Fatal("privileged developer config gate accepted scoped token") }
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			config := scopedTestConfig(ns, sub)
			change(config)
			if err := validateForNamespace(config, ns); err == nil {
				t.Fatal("unsafe scoped credentials accepted")
			}
		})
	}
	for _, ns := range []string{"default", "kube-system", "cka-../bad", "cka-", ""} {
		if _, err := LoadScopedLocalConfig("/nonexistent", ns); err == nil {
			t.Errorf("invalid namespace %q accepted", ns)
		}
	}
	if _, err := LoadScopedLocalConfig("/tmp/relative", "cka-payments"); err == nil {
		// A non-existent credential path must fail before attempting any default credentials.
		t.Fatal("missing kubeconfig accepted")
	}
}

