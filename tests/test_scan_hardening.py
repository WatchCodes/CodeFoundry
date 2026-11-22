import os
from pathlib import Path

import pytest

from codefoundry.scan import scan_repo, ScanResult


def _write(p: Path, content: str | bytes = "", *, encoding: str = "utf-8"):
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding=encoding)


def _base_repo(tmp_path: Path, name: str = "repo") -> Path:
    r = tmp_path / name
    r.mkdir()
    _write(r / "README.md", "hello\n")
    return r


def test_spdx_license_identifier_in_license_file(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "some text\nSPDX-License-Identifier: Apache-2.0\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "Apache-2.0"
    assert scan.license_file == "LICENSE"


def test_spdx_identifier_case_insensitive(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "spdx-license-identifier: MIT\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "MIT"


def test_spdx_identifier_takes_precedence_as_signal(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE.txt", "MIT License\n\nSPDX-License-Identifier: MIT\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "MIT"
    assert scan.license_file == "LICENSE.txt"


def test_copying_variant_detected(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "COPYING", "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "GPL-3.0-only"
    assert scan.license_file == "COPYING"


def test_licence_british_spelling_detected(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENCE.md", "MIT License\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "MIT"
    assert scan.license_file == "LICENCE.md"


def test_multiple_license_files_reported(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    _write(r / "COPYING", "GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    d = scan.to_dict()
    # additive evidence should expose multiple license files
    license_files = d.get("evidence", {}).get("license_files")
    assert isinstance(license_files, list)
    assert set(license_files) >= {"LICENSE", "COPYING"}
    # multiple different licenses is ambiguous -> fail-closed signal
    warnings = d.get("warnings", [])
    assert any("multiple" in w.lower() or "dual" in w.lower() or "license" in w.lower() for w in warnings)
    # detected license should not silently pick a single permissive one when ambiguous
    assert scan.detected_license in {None, "UNKNOWN", "NOASSERTION"}


def test_dual_license_spdx_expression_signal(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "SPDX-License-Identifier: MIT OR Apache-2.0\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    d = scan.to_dict()
    # fail-closed for ambiguous dual license
    assert scan.detected_license in {"UNKNOWN", "NOASSERTION"}
    evidence = d.get("evidence", {})
    # signal present
    assert evidence.get("dual_license_signal") is True or any("MIT OR Apache-2.0" in str(v) for v in evidence.values())
    warnings = d.get("warnings", [])
    assert any("dual" in w.lower() or "spdx" in w.lower() or "expression" in w.lower() for w in warnings)


def test_noassertion_handling(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "SPDX-License-Identifier: NOASSERTION\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "NOASSERTION"


def test_unknown_when_unrecognized(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "Some custom license text\nAll rights reserved maybe\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license == "UNKNOWN"
    assert scan.license_file == "LICENSE"


def test_no_license_file_returns_none(tmp_path: Path):
    r = _base_repo(tmp_path)
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert scan.detected_license is None
    assert scan.license_file is None


def test_structure_evidence_gitmodules(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    _write(r / ".gitmodules", '[submodule "vendor/x"]\n\tpath = vendor/x\n\turl = https://example.invalid/x.git\n')
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    ev = scan.to_dict()["evidence"]
    assert ev.get("has_gitmodules") is True
    assert ev.get("submodule_count", 0) >= 1


def test_structure_evidence_lfs_pointer(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    _write(r / "assets/model.bin", "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    ev = scan.to_dict()["evidence"]
    assert ev.get("lfs_pointer_count", 0) >= 1
    assert "assets/model.bin" in ev.get("lfs_pointer_files", [])


def test_structure_evidence_symlink(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    target = r / "README.md"
    link = r / "README.link"
    try:
        link.symlink_to(target.name)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    ev = scan.to_dict()["evidence"]
    assert ev.get("symlink_count", 0) >= 1
    assert "README.link" in ev.get("symlink_paths", [])


def test_structure_evidence_binary_count(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    _write(r / "bin.dat", b"\x00\x01\x02\x03\x04")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    ev = scan.to_dict()["evidence"]
    assert ev.get("binary_file_count", 0) >= 1


def test_structure_evidence_large_files(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    # create file larger than threshold (use 1MiB+)
    big = r / "big.bin"
    big.write_bytes(b"a" * (1024 * 1024 + 10))
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    ev = scan.to_dict()["evidence"]
    assert ev.get("large_file_count", 0) >= 1
    large_files = ev.get("large_files", [])
    assert any(entry.get("path") == "big.bin" for entry in large_files)


def test_scan_result_to_dict_additive_compat(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    scan = scan_repo(r, source="s/r", sha="abc123")
    d = scan.to_dict()
    # existing keys must be present with compatible values
    for k in ["source", "sha", "detected_license", "license_file", "file_count", "top_level", "has_readme", "has_dotgit", "evidence"]:
        assert k in d
    assert d["source"] == "s/r"
    assert d["sha"] == "abc123"
    assert d["detected_license"] == "MIT"
    assert d["license_file"] == "LICENSE"
    assert isinstance(d["file_count"], int)
    assert isinstance(d["top_level"], list)
    assert d["has_readme"] is True
    assert d["has_dotgit"] is False
    assert isinstance(d["evidence"], dict)
    # additive fields
    assert "warnings" in d
    assert isinstance(d["warnings"], list)


def test_warnings_field_present_on_scan_result(tmp_path: Path):
    r = _base_repo(tmp_path)
    _write(r / "LICENSE", "MIT License\n")
    scan = scan_repo(r, source="s/r", sha="0" * 40)
    assert hasattr(scan, "warnings")
    assert isinstance(scan.warnings, list)
    assert isinstance(scan.to_dict().get("warnings"), list)
