// Package observe reads only the dedicated local CKA development cluster.
// It cannot choose a production context or invoke kubeconfig credential plugins.
package observe

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/clientcmd/api"
)

const Context = "kind-cka-dev"
const ObserverAccount = "cka-grade-observer"

var scopedNamespace = regexp.MustCompile(`^cka-[a-z][a-z0-9-]*[a-z0-9]// Package observe reads only the dedicated local CKA development cluster.
// It cannot choose a production context or invoke kubeconfig credential plugins.
package observe

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/clientcmd/api"
)

)

// tokenSubject performs a preflight sanity check on the unverified JWT
// payload; the Kubernetes API server validates the signature and RBAC.
func tokenSubject(token string) (string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || len(token) > 16384 {
		return "", errors.New("invalid scoped token")
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(raw) > 4096 {
		return "", errors.New("invalid scoped token payload")
	}
	var claims struct { Subject string `json:"sub"` }
	if json.Unmarshal(raw, &claims) != nil || claims.Subject == "" {
		return "", errors.New("missing scoped token subject")
	}
	return claims.Subject, nil
}

func checkedLocalURL(server string) error {
	u, err := url.Parse(server)
	if err != nil || u.Scheme != "https" || u.User != nil ||
		u.Opaque != "" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("kind API must use a bare HTTPS endpoint")
	}
	host, port, err := net.SplitHostPort(u.Host)
	if err != nil || port == "" {
		return errors.New("kind API requires an explicit loopback port")
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return errors.New("Kubernetes API endpoint is not a loopback IP")
	}
	return nil
}

// validate refuses multi-context kubeconfig files, external paths, exec auth,
// insecure TLS and nonlocal API servers. No client is created on failure.
func validate(cfg *api.Config) error { return validateForNamespace(cfg, "") }

// validateForNamespace allows only the fixed developer certificate or one
// narrowly named, namespace-scoped service-account token. In both cases the
// TLS endpoint and kubeconfig topology remain fixed and local-only.
func validateForNamespace(cfg *api.Config, namespace string) error {
	if cfg == nil || cfg.CurrentContext != Context ||
		len(cfg.Contexts) != 1 || len(cfg.Clusters) != 1 || len(cfg.AuthInfos) != 1 {
		return errors.New("refusing kubeconfig outside fixed kind-cka-dev context")
	}
	ctx, ok := cfg.Contexts[Context]
	if !ok || ctx == nil || ctx.Cluster != Context || ctx.AuthInfo != Context ||
		ctx.Namespace != namespace {
		return errors.New("unexpected kind context definition")
	}
	cluster, ok := cfg.Clusters[Context]
	if !ok || cluster == nil || cluster.InsecureSkipTLSVerify ||
		cluster.TLSServerName != "" || cluster.ProxyURL != "" ||
		cluster.CertificateAuthority != "" || len(cluster.CertificateAuthorityData) == 0 {
		return errors.New("kind API TLS must use embedded CA data")
	}
	if err := checkedLocalURL(cluster.Server); err != nil {
		return err
	}
	user, ok := cfg.AuthInfos[Context]
	if !ok || user == nil || user.Exec != nil || user.AuthProvider != nil ||
		user.Impersonate != "" || len(user.ImpersonateGroups) != 0 ||
		len(user.ImpersonateUserExtra) != 0 || user.TokenFile != "" ||
		user.Username != "" || user.Password != "" ||
		user.ClientCertificate != "" || user.ClientKey != "" {
		return errors.New("kind auth must not use plugins, impersonation or credential files")
	}
	if namespace == "" {
		if user.Token != "" || len(user.ClientCertificateData) == 0 ||
			len(user.ClientKeyData) == 0 {
			return errors.New("developer kind auth requires embedded client cert/key")
		}
		return nil
	}
	if !scopedNamespace.MatchString(namespace) || len(namespace) > 55 ||
		len(user.ClientCertificateData) != 0 || len(user.ClientKeyData) != 0 {
		return errors.New("scoped kind auth requires a CKA namespace and token only")
	}
	subject, err := tokenSubject(user.Token)
	if err != nil || subject != fmt.Sprintf("system:serviceaccount:%s:%s", namespace, ObserverAccount) {
		return errors.New("scoped kind auth has wrong service-account subject")
	}
	return nil
}

// LoadLocalConfig accepts only a private, explicit kubeconfig file, normally
// <repo>/.local/kubeconfig produced by kind. Never load ~/.kube/config.
func LoadLocalConfig(path string) (*rest.Config, error) {
	return loadLocalConfig(path, "")
}

// LoadScopedLocalConfig requires a short-lived service-account bearer token
// for the exact scenario namespace. This is the only config accepted by
// the Go live observer CLI; privileged kind developer certs are refused.
func LoadScopedLocalConfig(path, namespace string) (*rest.Config, error) {
	if !scopedNamespace.MatchString(namespace) || len(namespace) > 55 {
		return nil, errors.New("invalid CKA scope")
	}
	return loadLocalConfig(path, namespace)
}

func loadLocalConfig(path, namespace string) (*rest.Config, error) {
	if !filepath.IsAbs(path) {
		return nil, errors.New("dedicated kubeconfig path must be absolute")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("dedicated kubeconfig must be a private regular file")
	}
	cfg, err := clientcmd.LoadFromFile(path)
	if err != nil {
		return nil, errors.New("cannot read dedicated kind kubeconfig")
	}
	if err := validateForNamespace(cfg, namespace); err != nil {
		return nil, err
	}
	restCfg, err := clientcmd.NewNonInteractiveClientConfig(
		*cfg, Context, &clientcmd.ConfigOverrides{}, nil).ClientConfig()
	if err != nil {
		return nil, errors.New("invalid dedicated kind client config")
	}
	if err := checkedLocalURL(restCfg.Host); err != nil {
		return nil, err
	}
	restCfg.Timeout = 10 * time.Second
	restCfg.QPS = 2
	restCfg.Burst = 2
	return restCfg, nil
}
