// Package observe reads only the dedicated local CKA development cluster.
// It cannot choose a production context or invoke kubeconfig credential plugins.
package observe

import (
	"errors"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/clientcmd/api"
)

const Context = "kind-cka-dev"

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
func validate(cfg *api.Config) error {
	if cfg == nil || cfg.CurrentContext != Context ||
		len(cfg.Contexts) != 1 || len(cfg.Clusters) != 1 || len(cfg.AuthInfos) != 1 {
		return errors.New("refusing kubeconfig outside fixed kind-cka-dev context")
	}
	ctx, ok := cfg.Contexts[Context]
	if !ok || ctx == nil || ctx.Cluster != Context || ctx.AuthInfo != Context ||
		ctx.Namespace != "" {
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
		len(user.ImpersonateUserExtra) != 0 ||
		user.Token != "" || user.TokenFile != "" ||
		user.Username != "" || user.Password != "" ||
		user.ClientCertificate != "" || user.ClientKey != "" ||
		len(user.ClientCertificateData) == 0 || len(user.ClientKeyData) == 0 {
		return errors.New("kind auth must use embedded client cert/key without plugins")
	}
	return nil
}

// LoadLocalConfig accepts only a private, explicit kubeconfig file, normally
// <repo>/.local/kubeconfig produced by kind. Never load ~/.kube/config.
func LoadLocalConfig(path string) (*rest.Config, error) {
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
	if err := validate(cfg); err != nil {
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
