// CI-ONLY cross-repository contract fixture. Never ship or run as a service.
// Import the *pinned ShadowOps Identity Go core* and use its actual staging
// issuer / JWKS key derivation. App-audience JWT signing is synthetic here:
// the real Identity service still CANNOT issue CKA user login tokens.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/evilsysadmin/shadowops-infra/services/identity/core"
)

const syntheticSubject = "11111111-1111-4111-8111-111111111111"

type claims struct {
	Iss string `json:"iss"`
	Aud string `json:"aud"`
	Sub string `json:"sub"`
	Iat int64  `json:"iat"`
	Nbf int64  `json:"nbf"`
	Exp int64  `json:"exp"`
}

type fixture struct {
	JWKS          json.RawMessage `json:"jwks"`
	Subject       string          `json:"subject"`
	Valid         string          `json:"valid"`
	Probe         string          `json:"probe"`
	WrongAudience string          `json:"wrong_audience"`
	WrongIssuer   string          `json:"wrong_issuer"`
	Expired       string          `json:"expired"`
	Future        string          `json:"future"`
	SessionBound  string          `json:"session_bound"`
}

func sign(priv ed25519.PrivateKey, kid string, body claims) (string, error) {
	head, err := json.Marshal(struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
		Kid string `json:"kid"`
	}{Alg: "EdDSA", Typ: "JWT", Kid: kid})
	if err != nil {
		return "", err
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return "", err
	}
	message := base64.RawURLEncoding.EncodeToString(head) + "." + base64.RawURLEncoding.EncodeToString(payload)
	sig := ed25519.Sign(priv, []byte(message))
	return message + "." + base64.RawURLEncoding.EncodeToString(sig), nil
}

func run() error {
	outfile := os.Getenv("CKA_IDENTITY_CONTRACT_FIXTURE_FILE")
	if outfile == "" {
		return errors.New("missing CI fixture output path")
	}
	seed := make([]byte, ed25519.SeedSize)
	if _, err := rand.Read(seed); err != nil {
		return err
	}
	identity, err := core.New("staging", seed)
	if err != nil {
		return err
	}
	publicJWKS, err := identity.JWKS()
	if err != nil {
		return err
	}
	var set core.KeySet
	if err := json.Unmarshal(publicJWKS, &set); err != nil || len(set.Keys) != 1 {
		return errors.New("invalid Identity public JWKS")
	}

	now := time.Now().UTC()
	epoch := now.Unix()
	base := claims{Iss: identity.Issuer(), Aud: "cka-simulator", Sub: syntheticSubject,
		Iat: epoch - 5, Nbf: epoch - 5, Exp: epoch + 300}
	privateKey := ed25519.NewKeyFromSeed(seed)
	makeToken := func(v claims) (string, error) {
		return sign(privateKey, set.Keys[0].Kid, v)
	}
	var result fixture
	result.JWKS = publicJWKS
	result.Subject = syntheticSubject
	result.Valid, err = makeToken(base)
	if err != nil {
		return err
	}
	result.Probe, err = identity.ProbeToken(now)
	if err != nil {
		return err
	}
	v := base
	v.Aud = "chronicles"
	result.WrongAudience, err = makeToken(v)
	if err != nil {
		return err
	}
	v = base
	v.Iss = "https://id.shadowops.dpdns.org"
	result.WrongIssuer, err = makeToken(v)
	if err != nil {
		return err
	}
	v = base
	v.Iat, v.Nbf, v.Exp = epoch-7200, epoch-7200, epoch-7100
	result.Expired, err = makeToken(v)
	if err != nil {
		return err
	}
	v = base
	v.Iat, v.Nbf, v.Exp = epoch+3600, epoch+3600, epoch+3900
	result.Future, err = makeToken(v)
	if err != nil {
		return err
	}
	result.SessionBound, err = sessionBoundVector(seed, publicJWKS)
	if err != nil {
		return err
	}

	encoded, err := json.Marshal(result)
	if err != nil {
		return err
	}
	// File is a disposable test artifact, NOT a signing key or a user session.
	f, err := os.OpenFile(outfile, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	if _, err := f.Write(encoded); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "Identity cross-repository contract fixture failed:", err)
		os.Exit(1)
	}
}
