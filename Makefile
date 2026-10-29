# Local Ollama / OCI A1 Workers AI scenario factory (never publish generated proposals).
.DEFAULT_GOAL := help
PYTHON ?= python
COMPOSE ?= docker compose -f compose.ollama.yml
PROVIDER ?= $(if $(CKA_SCENARIO_PROVIDER),$(CKA_SCENARIO_PROVIDER),ollama)
OLLAMA_MODEL ?= qwen2.5:7b-instruct
CF_AI_MODEL ?= @cf/meta/llama-3.3-70b-instruct-fp8-fast
MODEL ?= $(if $(filter workers-ai,$(PROVIDER)),$(CF_AI_MODEL),$(OLLAMA_MODEL))
TYPE ?= deployment_scale
COUNT ?= 1
PROPOSAL ?=
SLUG ?=
SHA256 ?=
REVIEWER ?=
OLLAMA_URL ?= http://127.0.0.1:11434
export OLLAMA_URL

.PHONY: help ollama-up ollama-down ollama-pull ollama-status scenario-generate scenario-audit scenario-batch scenario-verify scenario-review-queue scenario-prepare-review scenario-validate scenario-smoke scenario-check lab-up lab-down

help:
	@printf '%s\n' \
	  'make ollama-up                            Local Ollama container (CPU by default)' \
	  'make ollama-pull                          Pull OLLAMA_MODEL=$(OLLAMA_MODEL)' \
	  'make scenario-generate                    Generate proposals; local Ollama by default' \
	  'make scenario-generate PROVIDER=workers-ai Generate via Cloudflare Workers AI (OCI A1)' \
	  'make scenario-audit                       Offline batch YAML + duplicate check' \
	  'make scenario-batch                       Generate then audit; never auto-publish' \
	  'make scenario-verify                      Live smoke of <=5 audited proposals in dedicated kind' \
	  'make scenario-review-queue               Read-only verified proposals awaiting human review' \
	  'make scenario-prepare-review             Explicit private draft; requires SLUG SHA256 REVIEWER' \
	  'make scenario-validate PROPOSAL=scenarios/generated/<slug>.yaml' \
	  'make lab-up                               Create dedicated kind-cka-dev cluster manually' \
	  'make scenario-smoke PROPOSAL=scenarios/generated/<slug>.yaml' \
	  'make lab-down                             Remove dedicated local kind cluster' \
	  'make ollama-down                          Stop local Ollama (keep models)' \
	  'make scenario-check                       Test and validate curated scenario bank'

ollama-up:
	$(COMPOSE) up -d ollama

ollama-down:
	$(COMPOSE) down

ollama-pull: ollama-up
	$(COMPOSE) exec -T ollama ollama pull "$(OLLAMA_MODEL)"

ollama-status:
	$(COMPOSE) exec -T ollama ollama list

scenario-generate:
	$(PYTHON) -m cka_sim generate --provider "$(PROVIDER)" --model "$(MODEL)" --type "$(TYPE)" --count "$(COUNT)" --output scenarios/generated

scenario-audit:
	$(PYTHON) -m cka_sim audit-proposals --directory scenarios/generated

scenario-batch: scenario-generate
	$(MAKE) scenario-audit

scenario-verify:
	$(PYTHON) -m cka_sim verify-proposals


scenario-review-queue:
	$(PYTHON) -m cka_sim review-queue

scenario-prepare-review:
	@test -n "$(SLUG)" -a -n "$(SHA256)" -a -n "$(REVIEWER)" || { echo 'SLUG, SHA256, and REVIEWER are required' >&2; exit 2; }
	$(PYTHON) -m cka_sim prepare-review --slug "$(SLUG)" --sha256 "$(SHA256)" --reviewer "$(REVIEWER)"

scenario-validate:
	@test -n "$(PROPOSAL)" || { echo 'PROPOSAL=<path-to-generated-yaml> is required' >&2; exit 2; }
	$(PYTHON) -m cka_sim validate-proposal "$(PROPOSAL)"

scenario-smoke: scenario-validate
	$(PYTHON) -m cka_sim proposal-smoke "$(PROPOSAL)"

scenario-check:
	$(PYTHON) -m pytest
	$(PYTHON) -m cka_sim validate-bank

lab-up:
	$(PYTHON) -m cka_sim lab-up

lab-down:
	$(PYTHON) -m cka_sim lab-down
