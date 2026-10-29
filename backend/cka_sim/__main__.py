"""Explicit developer commands, fixed dedicated Kubernetes lab context only."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from .catalog import PROPOSALS, load_bank, load_proposal, save_generated
from .factory import run_factory
from .kube import LabError, cleanup, create_lab, delete_lab, grade, kubectl, setup, solve_for_smoke
from .ollama import GenerationError
from .providers import SUPPORTED_PROVIDERS, default_model, generate
from .pr_bundle import prepare_pr_bundle
from .quality import audit_proposals
from .review import review_queue
from .review_drafts import prepare_review_draft
from .verification import save_report, verify_batch



def _diagnose_smoke_failure(namespace: str) -> None:
    """Capture the failing pods/events before the lab namespace is deleted."""
    print(f"SMOKE FAILURE: diagnostics for {namespace}", file=sys.stderr)
    for args in (
        ["get", "pods", "-n", namespace, "-o", "wide"],
        ["describe", "pods", "-n", namespace],
        ["get", "events", "-n", namespace, "--sort-by=.metadata.creationTimestamp"],
    ):
        try:
            print(kubectl(args)[-16000:], file=sys.stderr)
        except LabError as exc:
            print(f"Unable to inspect {args[0]}: {exc}", file=sys.stderr)


def _smoke_scenario(s, *, quiet: bool = False) -> None:
    try:
        setup(s)
        before = grade(s)
        if before["passed"]:
            raise ValueError("Scenario must fail in its initial state")
        solve_for_smoke(s)
        for _ in range(30):
            after = grade(s)
            if after["passed"]:
                if not quiet:
                    print(f"PASS: {s.slug} starts broken and can be solved")
                break
            time.sleep(3)
        else:
            raise ValueError(f"Reference fix not graded as passing: {after}")
    except Exception:
        _diagnose_smoke_failure(s.namespace)
        raise
    finally:
        cleanup(s)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cka-sim")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-bank")
    factory = commands.add_parser("factory-run")
    factory.add_argument("--execute", action="store_true")
    factory.add_argument("--count", type=int, default=1)
    factory.add_argument("--type", choices=["deployment_scale", "service_selector"],
                         default="deployment_scale")
    factory.add_argument("--model", default=None)
    auditor = commands.add_parser("audit-proposals")
    auditor.add_argument("--directory", type=Path, default=PROPOSALS)
    commands.add_parser("verify-proposals")
    commands.add_parser("review-queue")
    bundle = commands.add_parser("prepare-pr-bundle")
    bundle.add_argument("--slug", required=True)
    commands.add_parser("lab-smoke-all")
    review = commands.add_parser("prepare-review")
    review.add_argument("--slug", required=True)
    review.add_argument("--sha256", required=True)
    review.add_argument("--reviewer", required=True)
    generator = commands.add_parser("generate")
    generator.add_argument("--provider", choices=SUPPORTED_PROVIDERS,
                           default=os.getenv("CKA_SCENARIO_PROVIDER", "ollama"))
    generator.add_argument("--model", default=None)
    generator.add_argument("--type", default="deployment_scale",
                           choices=["deployment_scale", "service_selector"])
    generator.add_argument("--output", type=Path, default=PROPOSALS)
    generator.add_argument("--count", type=int, default=1)
    validate = commands.add_parser("validate-proposal")
    validate.add_argument("path", type=Path)
    smoke_proposal = commands.add_parser("proposal-smoke")
    smoke_proposal.add_argument("path", type=Path)
    commands.add_parser("lab-up")
    commands.add_parser("lab-down")
    for name in ("lab-setup", "lab-grade", "lab-clean", "lab-smoke"):
        cmd = commands.add_parser(name)
        cmd.add_argument("slug")
    args = parser.parse_args()
    try:
        if args.command == "validate-bank":
            bank = load_bank()
            if not bank:
                raise ValueError("No curated scenarios found")
            print(f"Validated {len(bank)} curated scenarios")
        elif args.command == "factory-run":
            options = {"execute": args.execute, "count": args.count,
                       "scenario_type": args.type}
            if args.model is not None:
                options["model"] = args.model
            print(json.dumps(run_factory(**options), indent=2, ensure_ascii=False))
        elif args.command == "audit-proposals":
            result = audit_proposals(args.directory)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if not result["passed"]:
                raise SystemExit(1)
        elif args.command == "verify-proposals":
            report = verify_batch(smoke=lambda s: _smoke_scenario(s, quiet=True))
            save_report(report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            print("Local verification evidence: .local/proposal-verification.json",
                  file=sys.stderr)
            if not report["passed"]:
                raise SystemExit(1)
        elif args.command == "review-queue":
            print(json.dumps(review_queue(), indent=2, ensure_ascii=False))
        elif args.command == "prepare-pr-bundle":
            folder = prepare_pr_bundle(slug=args.slug)
            print(f"PR candidate bundle (NOT PUBLISHED): {folder}")
        elif args.command == "lab-smoke-all":
            bank = load_bank()
            if not bank:
                raise ValueError("No curated scenarios found")
            for s in bank.values():
                _smoke_scenario(s)
            print(f"PASS: all {len(bank)} curated scenarios passed live kind smoke")
        elif args.command == "prepare-review":
            path = prepare_review_draft(slug=args.slug,
                                        approved_sha256=args.sha256,
                                        reviewed_by=args.reviewer)
            print(f"Private review draft (NOT PUBLISHED): {path}")
        elif args.command == "generate":
            if not 1 <= args.count <= 20:
                raise ValueError("--count must be between 1 and 20")
            for _ in range(args.count):
                # Never publish proposals to the curated bank.
                # Retry only slug/namespace collisions with older proposals.
                for attempt in range(3):
                    s = generate(provider=args.provider, model=args.model or default_model(args.provider),
                                 scenario_type=args.type)
                    try:
                        path = save_generated(s, args.output)
                        print(f"Proposal quarantined for review: {path}")
                        break
                    except (FileExistsError, ValueError) as exc:
                        if attempt == 2:
                            raise ValueError(f"Proposal collision after retries: {exc}") from exc
        elif args.command == "validate-proposal":
            s = load_proposal(args.path)
            print(f"VALID DSL/policy: {s.slug} (NOT live-smoked or published)")
        elif args.command == "proposal-smoke":
            # Refuse paths outside the explicit quarantine, even when requested by CLI.
            if not args.path.resolve().is_relative_to(PROPOSALS.resolve()):
                raise ValueError("Only quarantined proposals can run a smoke test")
            s = load_proposal(args.path)
            _smoke_scenario(s)
            print("SMOKE passed; human review still required before publication")
        elif args.command == "lab-up":
            print(create_lab())
        elif args.command == "lab-down":
            print(delete_lab())
        else:
            s = load_bank()[args.slug]
            if args.command == "lab-setup":
                setup(s)
                print(f"Prepared {s.slug} in dedicated kind context")
            elif args.command == "lab-grade":
                print(json.dumps(grade(s), indent=2, ensure_ascii=False))
            elif args.command == "lab-clean":
                cleanup(s)
            elif args.command == "lab-smoke":
                _smoke_scenario(s)
    except (LabError, GenerationError, ValueError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
