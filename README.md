# CKA Exam Simulator

Hands-on Kubernetes practice: real labs, deterministic grading and local Ollama-powered scenario proposals.

## First milestone: scenario core
This iteration includes a strict scenario DSL, two curated exercises, deterministic state grading, trusted manifest fixtures and a local Ollama generator. It does not yet expose live labs or browser terminals.

Setup:
- python -m pip install -e '.[dev]'
- python -m pytest
- python -m cka_sim validate-bank
- python -m cka_sim generate --model YOUR_LOCAL_MODEL --type deployment_scale

Generated proposals are quarantined under scenarios/generated; promotion requires real-cluster failing/passing smoke and human review. Do not host this prototype publicly. Next milestone: local kind runner and Kubernetes state reads, then browser terminal.
