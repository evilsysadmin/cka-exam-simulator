"""Minimal CLI; no Kubernetes or other privileged commands in this milestone."""
import argparse
from pathlib import Path
from .catalog import load_bank, save_generated
from .ollama import generate


def main():
    parser = argparse.ArgumentParser(prog="cka-sim")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-bank")
    g = commands.add_parser("generate")
    g.add_argument("--model", required=True)
    g.add_argument("--type", default="deployment_scale",
                   choices=["deployment_scale", "service_selector"])
    args = parser.parse_args()
    if args.command == "validate-bank":
        bank = load_bank()
        if not bank:
            parser.error("No curated scenarios found")
        print(f"Validated {len(bank)} curated scenarios")
    else:
        scenario = generate(model=args.model, scenario_type=args.type)
        path = save_generated(scenario, Path("scenarios/generated"))
        print(f"Proposal saved for review: {path}")


if __name__ == "__main__":
    main()
