"""Regression tests: MigrationRequest.license is advisory; scan stage is the gate.

RepoAtlas cannot know a repository's license at discovery time and emits
NOASSERTION. CodeFoundry detects the real license in its scan stage and gates
there. Intake must accept unknown request license values, and a wrong or
missing request license must never let a repo bypass the scan gate.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from codefoundry.intake import validate_migration_request
from codefoundry.pipeline import run_slice

ENGINE = os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")
FIXTURES = Path(__file__).parent / "fixtures" / "lab"


def _request(**overrides):
    base = {
        "schema_version": 1,
        "batch_id": "license-advisory",
        "source": "Test/Repo",
        "destination_owner": "WatchCodes",
        "target_name": "nastech-license-advisory",
        "sha": "a" * 40,
        "license": "NOASSERTION",
        "action": "create",
        "provenance": {
            "origin": "allowlisted",
            "discovered_in": "test/repo",
            "manifest_batch_id": "license-advisory",
        },
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("license_value", ["NOASSERTION", "OTHER", None])
def test_intake_accepts_unknown_request_license(license_value):
    doc = validate_migration_request(_request(license=license_value))
    assert doc["license"] == license_value


def _make_repo(tmp_path: Path, *, name: str, license_text: str | None) -> tuple[Path, str]:
    src = tmp_path / name
    src.mkdir()
    (src / "README.md").write_text("License advisory test repo\n", encoding="utf-8")
    if license_text is not None:
        (src / "LICENSE").write_text(license_text, encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.name", "t"],
        ["config", "user.email", "t@t"],
        ["add", "-A"],
        ["commit", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=src, check=True, capture_output=True, text=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True
    ).stdout.strip()
    return src, sha


def _slice(src: Path, sha: str, *, request_license):
    return run_slice(
        _request(source="Test/Repo", sha=sha, license=request_license),
        dry_run=True,
        engine_path=ENGINE,
        clone_source=str(src),
    )


def test_scan_gate_detects_license_despite_noassertion_request(tmp_path: Path):
    mit_license = (FIXTURES / "sample.LICENSE.MIT").read_text(encoding="utf-8")
    src, sha = _make_repo(tmp_path, name="mit", license_text=mit_license)
    report = _slice(src, sha, request_license="NOASSERTION")
    # REQUIRES_REVIEW is only from dry_run's created=False, not a license block.
    assert report["license"]["detected"] == "MIT"
    assert report["license"]["review_required"] is False
    assert "license" not in (report["error"] or "").lower()


def test_scan_gate_rejects_missing_license_despite_optimistic_request(tmp_path: Path):
    src, sha = _make_repo(tmp_path, name="nolicense", license_text=None)
    report = _slice(src, sha, request_license="MIT")
    assert report["status"] == "FAILED"
    assert "license" in (report["error"] or "").lower()
