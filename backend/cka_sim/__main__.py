"""Explicit developer commands, fixed dedicated Kubernetes lab context only."""
import argparse
import json
from pathlib import Path
import sys
import time

from .catalog import load_bank, save_generated
from .kube import LabError, cleanup, create_lab, delete_lab, grade, setup, solve_for_smoke
from .ollama import GenerationError, generate


def main() -> None:
    parser = argparse.ArgumentParser(prog="cka-sim")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-bank")
    generator = commands.add_parser("generate")
    generator.add_argument("--model", required=True)
    generator.add_argument("--type", default="deployment_scale",
                           choices=["deployment_scale", "service_selector"])
    generator.add_argument("--output", type=Path, default=Path("scenarios/generated"))
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
        elif args.command == "generate":
            s = generate(model=args.model, scenario_type=args.type)
            path = save_generated(s, args.output)
            print(f"Proposal quarantined for review: {path}")
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
                try:
                    setup(s)
                    before = grade(s)
                    if before["passed"]:
                        raise ValueError("Scenario must fail in its initial state")
                    solve_for_smoke(s)
                    for _ in range(30):
                        after = grade(s)
                        if after["passed"]:
                            print(f"PASS: {s.slug} starts broken and can be solved")
                            break
                        time.sleep(3)
                    else:
                        raise ValueError(f"Reference fix not graded as passing: {after}")
                finally:
                    cleanup(s)
    except (LabError, GenerationError, ValueError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
