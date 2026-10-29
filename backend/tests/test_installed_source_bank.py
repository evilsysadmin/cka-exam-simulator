"""Read-only source bank selection for normal installed wheels and local editing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cka_sim.catalog import BANK, ROOT


def inspect_root(env_value: str, *, cwd: Path):
    env = os.environ.copy()
    env["CKA_SCENARIO_SOURCE_ROOT"] = str(env_value)
    return subprocess.run(
        [sys.executable, "-I", "-c",
         "import json; from cka_sim.catalog import ROOT, load_bank; "
         "print(json.dumps({'root': str(ROOT), 'scenarios': sorted(load_bank())}))"],
        capture_output=True, text=True, cwd=cwd, env=env, check=False)


def make_bank(tmp_path):
    source = tmp_path / "immutable-source"
    bank = source / "scenarios"
    bank.mkdir(parents=True)
    shutil.copy2(BANK / "scale-payment-api.json",
                 bank / "scale-payment-api.json")
    return source


def test_external_readonly_bank_is_discovered_even_from_another_cwd(tmp_path):
    source = make_bank(tmp_path)
    other_cwd = tmp_path / "unrelated"
    other_cwd.mkdir()
    report = inspect_root(str(source), cwd=other_cwd)
    assert report.returncode == 0, report.stderr
    data = json.loads(report.stdout)
    assert data == {"root": str(source), "scenarios": ["scale-payment-api"]}
    assert not (source / ".local").exists()
    assert not (source / "scenarios" / "generated").exists()


@pytest.mark.parametrize("bad_value", ["", "relative/source", "/tmp/../tmp/never-pinned"])
def test_explicit_invalid_source_root_never_falls_back(tmp_path, bad_value):
    report = inspect_root(bad_value, cwd=tmp_path)
    assert report.returncode != 0
    assert "CKA source root must be an existing absolute directory" in report.stderr


def test_existing_source_with_missing_bank_fails_closed(tmp_path):
    source = tmp_path / "empty-source"
    source.mkdir()
    report = inspect_root(str(source), cwd=tmp_path)
    assert report.returncode != 0
    assert "CKA source root must contain a scenarios directory" in report.stderr


@pytest.mark.parametrize("target", ["source", "parent", "scenarios"])
def test_source_root_denies_symlink_path_components(tmp_path, target):
    source = make_bank(tmp_path)
    if target == "source":
        linked = tmp_path / "linked-source"
        linked.symlink_to(source, target_is_directory=True)
        attempted = linked
    elif target == "parent":
        linked = tmp_path / "linked-parent"
        linked.symlink_to(tmp_path, target_is_directory=True)
        attempted = linked / source.name
    else:
        original = source / "scenarios"
        renamed = source / "real-bank"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        attempted = source
    report = inspect_root(str(attempted), cwd=tmp_path)
    assert report.returncode != 0
    assert "must not traverse symlinks" in report.stderr


def test_local_source_default_remains_unchanged():
    assert ROOT.is_dir()
    assert BANK == ROOT / "scenarios"
    assert (BANK / "scale-payment-api.json").is_file()
