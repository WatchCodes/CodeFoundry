"""Regression tests: copyleft licenses must block shipment and require review.

CODEFOUNDRY_PLAN.md requires copyleft to end in REQUIRES_REVIEW. Previously only
NOASSERTION/OTHER were flagged; GPL/AGPL/LGPL/MPL were 'preserved' with
review_required=False and proceeded to ship and could reach VERIFIED.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from codefoundry.pipeline import _license_obj, run_slice

ENGINE = os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")
FIXTURES = Path(__file__).parent / "fixtures" / "lab"


@pytest.mark.parametrize(
    "spdx",
    [
        "GPL-3.0-only",
        "GPL-2.0-only",
        "AGPL-3.0-only",
        "LGPL-3.0-only",
        "LGPL-2.1-only",
        "MPL-2.0",
    ],
)
def test_copyleft_spdx_requires_review(spdx: str):
    lic = _license_obj(spdx)
    assert lic["preserved"] is True
    assert lic["review_required"] is True


@pytest.mark.parametrize("spdx", ["MIT", "Apache-2.0", "BSD-3-Clause", "ISC"])
def test_permissive_spdx_does_not_require_review(spdx: str):
    lic = _license_obj(spdx)
    assert lic["preserved"] is True
    assert lic["review_required"] is False


def test_copyleft_slice_is_review_and_never_ships(tmp_path: Path, monkeypatch):
    # Build a real local repo with a GPL-3.0 license.
    src = tmp_path / "copyleft"
    src.mkdir()
    (src / "README.md").write_text("Copyleft repo\n", encoding="utf-8")
    (src / "LICENSE").write_text(
        (FIXTURES / "sample.LICENSE.GPL3").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for args in (
        ["init"],
        ["config", "user.name", "t"],
        ["config", "user.email", "t@t"],
        ["add", "-A"],
        ["commit", "-m", "init"],
    ):
        import subprocess

        subprocess.run(["git", *args], cwd=src, check=True, capture_output=True, text=True)
    import subprocess

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True
    ).stdout.strip()

    def _ship_must_not_run(*args, **kwargs):
        raise AssertionError("copyleft repository must never be shipped")

    monkeypatch.setattr("codefoundry.pipeline.ship_private_repo", _ship_must_not_run)

    report = run_slice(
        {
            "schema_version": 1,
            "batch_id": "copyleft-batch",
            "source": "Test/Copyleft",
            "destination_owner": "WatchCodes",
            "target_name": "nastech-copyleft",
            "sha": sha,
            "license": "GPL-3.0-only",
            "action": "create",
            "provenance": {
                "origin": "allowlisted",
                "discovered_in": "test/copyleft",
                "manifest_batch_id": "copyleft-batch",
            },
        },
        dry_run=True,
        engine_path=ENGINE,
        clone_source=str(src),
    )

    assert report["status"] == "REQUIRES_REVIEW"
    assert report["license"]["detected"] == "GPL-3.0-only"
    # Must be blocked at the license gate, not merely by dry-run's created=False.
    assert report["license"]["review_required"] is True
    assert report["error"] and "license" in report["error"].lower()
