"""Versioned curated bank and quarantined generated scenarios."""
import json
from pathlib import Path
from .models import Scenario

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "scenarios"


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


def save_generated(s: Scenario, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{s.slug}.json"
    with path.open("x", encoding="utf-8") as fp:
        json.dump(s.model_dump(mode="json"), fp, indent=2, ensure_ascii=False)
        fp.write("\n")
    return path
