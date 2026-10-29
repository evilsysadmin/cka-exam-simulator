// CI-only Mongo-backed versioned-session fixture. Imported by the Go to
// Python contract test; never used by CKA application or Identity production.
package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"time"

	"github.com/evilsysadmin/shadowops-infra/services/identity/issuance"
	"github.com/evilsysadmin/shadowops-infra/services/identity/password"
	"github.com/evilsysadmin/shadowops-infra/services/identity/revocation"
	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	ciMongoURL  = "mongodb://127.0.0.1:27018/?replicaSet=identity-rs&directConnection=true"
	ciCandidate = "ephemeral-CI-only-identity-cka-candidate"
)

// sessionBoundVector calls the ACTUAL Identity staging credential issuer and
// CKAPoCSigner on a disposable localhost replica set. In particular, it
// must not manufacture a JWT by calling the standalone fixture signer.
func sessionBoundVector(seed, expectedJWKS []byte) (string, error) {
	if os.Getenv("CKA_IDENTITY_MONGO_REPLICA_TEST_URL") != ciMongoURL {
		return "", errors.New("refusing non-local Identity Mongo test target")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()

	client, err := mongo.Connect(options.Client().ApplyURI(ciMongoURL))
	if err != nil {
		return "", err
	}
	defer client.Disconnect(context.Background())
	if err := client.Ping(ctx, nil); err != nil {
		return "", err
	}
	db := client.Database("shadowops_identity_staging")
	credentialHash, err := password.HashNew(ciCandidate)
	if err != nil {
		return "", err
	}
	_, err = db.Collection("pending_accounts").InsertOne(ctx, bson.D{
		{Key: "_id", Value: syntheticSubject},
		{Key: "environment", Value: "staging"},
		{Key: "state", Value: "active"},
		{Key: "credentials_disabled", Value: false},
		{Key: "credential_hash", Value: credentialHash},
		{Key: "credential_version", Value: int64(1)},
		{Key: "session_version", Value: int64(1)},
	})
	if err != nil {
		return "", err
	}
	issuer, err := issuance.NewStagingIssuer(ctx, client)
	if err != nil {
		return "", err
	}
	// Prove the credential check denies a wrong password before minting.
	if handle, denied := issuer.Issue(ctx, syntheticSubject, "wrong-ci-password"); handle != "" ||
		!errors.Is(denied, issuance.ErrDenied) {
		return "", errors.New("incorrect synthetic credential was accepted")
	}
	handle, err := issuer.Issue(ctx, syntheticSubject, ciCandidate)
	if err != nil {
		return "", err
	}
	signer, err := issuance.NewCKAPoCSigner(issuer, seed)
	if err != nil {
		return "", err
	}
	publicJWKS, err := signer.JWKS()
	if err != nil || !bytes.Equal(expectedJWKS, publicJWKS) {
		return "", errors.New("session issuer JWKS diverged from Identity Go core")
	}
	signed, err := signer.IssuePoCToken(ctx, handle)
	if err != nil {
		return "", err
	}
	if signed == "" {
		return "", errors.New("session-bound issuer returned an empty token")
	}
	// Strict synthetic revocation after issuing the vector. Previous JWT
	// remains signed; Python verifier MUST NOT treat it as current access.
	revoker, err := revocation.NewStagingRevoker(client)
	if err != nil {
		return "", err
	}
	if err := revoker.SuspendAndRevoke(ctx, syntheticSubject, 1); err != nil {
		return "", err
	}
	if next, denied := signer.IssuePoCToken(ctx, handle); next != "" ||
		!errors.Is(denied, issuance.ErrDenied) {
		return "", errors.New("revoked session issued another CKA token")
	}
	if next, denied := issuer.Issue(ctx, syntheticSubject, ciCandidate); next != "" ||
		!errors.Is(denied, issuance.ErrDenied) {
		return "", errors.New("suspended account issued another session")
	}
	return signed, nil
}
