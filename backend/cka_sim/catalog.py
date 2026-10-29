"""Curated JSON bank and separately quarantined YAML proposals from Ollama."""
import json
from pathlib import Path

import yaml
from yaml.events import AliasEvent
from .models import Scenario

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "scenarios"
PROPOSALS = BANK / "generated"
MAX_PROPOSAL_BYTES = 16_384


def load_bank(directory: Path = BANK) -> dict[str, Scenario]:
    scenarios: dict[str, Scenario] = {}
    for path in sorted(directory.glob("*.json")):
        s = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != s.slug:
            raise ValueError(f"File name and slug differ: {path.name}")
        if s.slug in scenarios:
            raise ValueError(f"Repeated slug: {s.slug}")
        scenarios[s.slug] = s
    return scenarios


class _UniqueKeysLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently overwriting policy fields."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Duplicate or non-string YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _check_curated_collisions(s: Scenario) -> None:
    for curated in load_bank().values():
        if s.slug == curated.slug or s.namespace == curated.namespace:
            raise ValueError("Proposal collides with a curated slug or namespace")


def load_proposal(path: Path) -> Scenario:
    """Parse data only; no commands, YAML objects, or candidate publication."""
    if path.suffix != ".yaml" or path.is_symlink():
        raise ValueError("Proposal must be a regular .yaml file (not a symlink)")
    if path.stat().st_size > MAX_PROPOSAL_BYTES:
        raise ValueError("YAML proposal exceeds size limit")
    raw = path.read_text(encoding="utf-8")
    try:
        if any(isinstance(event, AliasEvent) or event.anchor is not None
               for event in yaml.parse(raw)):
            raise ValueError("YAML aliases and anchors are not allowed")
        data = yaml.load(raw, Loader=_UniqueKeysLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid proposal YAML: {exc}") from exc
    scenario = Scenario.model_validate(data)
    if path.stem != scenario.slug:
        raise ValueError("YAML filename must match proposal slug")
    _check_curated_collisions(scenario)
    return scenario


def save_generated(s: Scenario, directory: Path = PROPOSALS) -> Path:
    """Save only validated DSL as YAML under a quarantine directory."""
    s = Scenario.model_validate(s.model_dump(mode="json"))
    _check_curated_collisions(s)
    directory.mkdir(parents=True, exist_ok=True)
    for existing in sorted(directory.glob("*.yaml")):
        previous = load_proposal(existing)
        if previous.namespace == s.namespace:
            raise ValueError("Another proposal already uses this namespace")
    path = directory / f"{s.slug}.yaml"
    with path.open("x", encoding="utf-8") as fp:
        yaml.safe_dump(s.model_dump(mode="json"), fp, allow_unicode=True,
                       sort_keys=False)
    return path
