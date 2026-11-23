import os
import subprocess
from pathlib import Path

import pytest

from codefoundry.brand import BrandError, BrandResult, run_brand
from codefoundry.credits import ensure_credits, ensure_notice


ENGINE = os.environ.get("CODEFOUNDRY_100WAYS_PATH", "/home/nascode/work/100ways")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "src", files: dict[str, str | bytes] | None = None) -> Path:
    src = tmp_path / name
    src.mkdir()
    for rel, content in (files or {}).items():
        fp = src / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            fp.write_bytes(content)
        else:
            fp.write_text(content, encoding="utf-8")
    _git(src, "init")
    _git(src, "config", "user.name", "t")
    _git(src, "config", "user.email", "t@t")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    return src


def snapshot(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def test_brand_result_evidence_keys():
    r = BrandResult(
        engine_ref="abc",
        files_changed=1,
        replacements=2,
        scan_passed=True,
        scan_violations=[],
        log="x",
        changed_paths=["a.txt"],
        engine_path="/engine",
    )
    d = r.to_dict()
    assert d["engine_ref"] == "abc"
    assert d["engine_path"] == "/engine"
    assert d["files_changed"] == 1
    assert d["changed_paths"] == ["a.txt"]
    assert d["changed_files"] == ["a.txt"]
    assert d["scan_passed"] is True
    assert d["scan_violations"] == []
    assert d["violations"] == []


def test_dry_run_makes_no_mutations(tmp_path: Path):
    src = make_repo(
        tmp_path,
        files={
            "README.md": "Hello Hermes\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
            "hermes.txt": "data\n",
        },
    )
    before = snapshot(src)
    result = run_brand(src, engine_path=ENGINE, dry_run=True)
    after = snapshot(src)
    assert before == after
    assert result.files_changed >= 1
    assert result.changed_paths
    # Dry run does not mutate disk; scan reflects on-disk state.
    assert isinstance(result.scan_passed, bool)
    assert isinstance(result.scan_violations, list)


def test_dry_run_reports_evidence(tmp_path: Path):
    src = make_repo(
        tmp_path,
        files={
            "README.md": "Hello Hermes\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
        },
    )
    result = run_brand(src, engine_path=ENGINE, dry_run=True)
    d = result.to_dict()
    assert isinstance(d["engine_ref"], str) and d["engine_ref"]
    assert isinstance(d["engine_path"], str) and d["engine_path"]
    assert isinstance(d["violations"], list)
    assert isinstance(d["changed_files"], list)
    assert isinstance(d["changed_paths"], list)
    assert isinstance(d["scan_passed"], bool)
    assert d["files_changed"] == len(d["changed_paths"])


def test_apply_brand_changes_content_and_keeps_clean(tmp_path: Path):
    src = make_repo(
        tmp_path,
        files={
            "README.md": "Hello Hermes\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
        },
    )
    result = run_brand(src, engine_path=ENGINE, dry_run=False)
    assert result.scan_passed is True
    assert result.scan_violations == []
    assert "Hermes" not in (src / "README.md").read_text(encoding="utf-8")


def test_binary_files_safe(tmp_path: Path):
    binary = b"\x00\x01Hermes\xff\x00binary"
    src = make_repo(
        tmp_path,
        files={
            "README.md": "Hello\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
            "blob.bin": binary,
        },
    )
    result = run_brand(src, engine_path=ENGINE, dry_run=False)
    assert (src / "blob.bin").read_bytes() == binary
    assert result.scan_passed is True


def test_binary_files_safe_dry_run(tmp_path: Path):
    binary = b"\x00Hermes\x00"
    src = make_repo(
        tmp_path,
        files={
            "README.md": "Hello\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted...\n",
            "blob.bin": binary,
        },
    )
    before = snapshot(src)
    result = run_brand(src, engine_path=ENGINE, dry_run=True)
    after = snapshot(src)
    assert before == after
    assert (src / "blob.bin").read_bytes() == binary
    assert isinstance(result.scan_violations, list)


def test_missing_repo_raises(tmp_path: Path):
    with pytest.raises(BrandError, match="repo_dir not found"):
        run_brand(tmp_path / "nope", engine_path=ENGINE)


def test_engine_not_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = make_repo(
        tmp_path,
        files={"README.md": "Hi\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    monkeypatch.setenv("CODEFOUNDRY_100WAYS_PATH", str(tmp_path / "missing-env"))
    # Force default candidate to be absent by making is_file return False for default rules.py
    import codefoundry.brand as brand_mod

    real_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        s = str(self)
        if s.endswith("hundredways/rules.py") and "/home/nascode/work/100ways" in s:
            return False
        return real_is_file(self)

    monkeypatch.setattr(brand_mod.Path, "is_file", fake_is_file)
    with pytest.raises(BrandError, match="100Ways engine not found"):
        run_brand(src, engine_path=tmp_path / "missing-engine")


def test_engine_env_path_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = make_repo(
        tmp_path,
        files={"README.md": "Hello Hermes\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    monkeypatch.setenv("CODEFOUNDRY_100WAYS_PATH", ENGINE)
    result = run_brand(src, engine_path=None, dry_run=True)
    assert result.engine_path
    assert Path(result.engine_path).resolve() == Path(ENGINE).resolve()


def test_engine_ref_is_reported(tmp_path: Path):
    src = make_repo(
        tmp_path,
        files={"README.md": "Hello\n", "LICENSE": "MIT License\n\nPermission is hereby granted...\n"},
    )
    result = run_brand(src, engine_path=ENGINE, dry_run=True)
    assert result.engine_ref
    assert result.engine_ref != ""
    assert "engine_ref" in result.to_dict()


def test_credits_idempotent_append_only(tmp_path: Path):
    make_repo(tmp_path, files={"LICENSE": "MIT License\n\nPermission is hereby granted...\n"})
    p1 = ensure_credits(tmp_path / "src", source="A/B", sha="0" * 40, license_spdx="MIT")
    p2 = ensure_credits(tmp_path / "src", source="A/B", sha="0" * 40, license_spdx="MIT")
    assert p1 == p2
    text = p1.read_text(encoding="utf-8")
    assert text.count("- Derived from A/B at " + "0" * 40 + " (MIT).") == 1
    assert text.endswith("(MIT).\n")


def test_credits_multiple_sources_append(tmp_path: Path):
    root = make_repo(tmp_path, files={"LICENSE": "MIT License\n"})
    ensure_credits(root, source="A/B", sha="1" * 40, license_spdx="MIT")
    ensure_credits(root, source="C/D", sha="2" * 40, license_spdx="Apache-2.0")
    text = (root / "CREDITS.md").read_text(encoding="utf-8")
    assert text.index("A/B") < text.index("C/D")
    assert text.count("- Derived from") == 2


def test_notice_idempotent_append_only(tmp_path: Path):
    root = make_repo(tmp_path, files={"LICENSE": "MIT License\n"})
    n1 = ensure_notice(root, source="A/B", sha="0" * 40)
    n2 = ensure_notice(root, source="A/B", sha="0" * 40)
    assert n1 == n2
    text = n1.read_text(encoding="utf-8")
    assert text.count("Derived from A/B at " + "0" * 40 + ".") == 1


def test_notice_appends_without_clobbering(tmp_path: Path):
    root = make_repo(tmp_path, files={"LICENSE": "MIT License\n"})
    notice = root / "NOTICE"
    notice.write_text("Existing upstream notice\n", encoding="utf-8")
    ensure_notice(root, source="A/B", sha="1" * 40)
    text = notice.read_text(encoding="utf-8")
    assert "Existing upstream notice" in text
    assert text.index("Existing upstream notice") < text.index("Derived from A/B")
    assert text.count("Derived from A/B") == 1


def test_license_preserved_untouched(tmp_path: Path):
    license_text = "MIT License\n\nPermission is hereby granted...\n"
    root = make_repo(tmp_path, files={"LICENSE": license_text})
    before = (root / "LICENSE").read_bytes()
    ensure_credits(root, source="A/B", sha="0" * 40, license_spdx="MIT")
    ensure_notice(root, source="A/B", sha="0" * 40)
    after = (root / "LICENSE").read_bytes()
    assert before == after


def test_credits_require_existing_repo_dir(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ensure_credits(tmp_path / "missing", source="A/B", sha="0" * 40, license_spdx="MIT")


def test_notice_require_existing_repo_dir(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ensure_notice(tmp_path / "missing", source="A/B", sha="0" * 40)
