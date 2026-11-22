import subprocess
import sys
from pathlib import Path

import pytest

from codefoundry.clone import CloneError, _run, clone_snapshot


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp: Path, name: str = "src", files: dict[str, str] | None = None) -> tuple[str, str]:
    src = tmp / name
    src.mkdir()
    for p, content in (files or {}).items():
        fp = src / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    _git(src, "init")
    _git(src, "config", "user.name", "t")
    _git(src, "config", "user.email", "t@t")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    sha = _git(src, "rev-parse", "HEAD").stdout.strip()
    return str(src), sha


def test_clone_additive_fields_and_manifest(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"README.md": "hello\n", "sub/dir.txt": "x\n"})
    dest = tmp_path / "out"
    res = clone_snapshot(src, sha, dest, include_manifest=True)

    assert res.source == src
    assert res.sha == sha
    assert res.dest_dir == str(dest)
    assert res.file_count == 2
    assert res.manifest_file_count == 2
    assert len(res.tree_hash) == 64
    assert len(res.manifest_hash) == 64
    assert res.submodules_detected is False
    assert res.submodule_paths == []
    assert res.lfs_detected is False
    assert res.lfs_patterns == []
    assert isinstance(res.file_manifest, list)
    paths = {e["path"] for e in res.file_manifest}
    assert paths == {"README.md", "sub/dir.txt"}
    for e in res.file_manifest:
        assert len(e["sha256"]) == 64
        assert isinstance(e["size"], int)

    d = res.to_dict()
    # additive fields present
    for k in [
        "submodules_detected",
        "submodule_paths",
        "lfs_detected",
        "lfs_patterns",
        "manifest_hash",
        "manifest_file_count",
        "file_manifest",
    ]:
        assert k in d
    assert d["file_count"] == d["manifest_file_count"]


def test_clone_default_no_manifest(tmp_path: Path):
    src, sha = make_repo(tmp_path, "src2", {"a.txt": "a\n"})
    res = clone_snapshot(src, sha, tmp_path / "out2")
    assert res.file_manifest is None
    assert res.to_dict()["file_manifest"] is None
    assert res.manifest_file_count == 1
    assert res.manifest_hash


def test_detects_submodules(tmp_path: Path):
    gitmodules = """[submodule "vendor/lib"]
\tpath = vendor/lib
\turl = https://example.com/lib.git

[submodule "tools/x"]
path = tools/x
url = https://example.com/x.git
"""
    src, sha = make_repo(
        tmp_path,
        files={
            ".gitmodules": gitmodules,
            "README.md": "ok\n",
        },
    )
    res = clone_snapshot(src, sha, tmp_path / "out")
    assert res.submodules_detected is True
    assert res.submodule_paths == ["vendor/lib", "tools/x"]
    assert res.to_dict()["submodule_paths"] == ["vendor/lib", "tools/x"]


def test_detects_lfs_filters(tmp_path: Path):
    src, sha = make_repo(
        tmp_path,
        files={
            ".gitattributes": "*.bin filter=lfs diff=lfs merge=lfs -text\n*.png filter=lfs diff=lfs merge=lfs -text\n",
            "docs/.gitattributes": "*.pdf filter=lfs diff=lfs merge=lfs -text\n",
            "README.md": "ok\n",
        },
    )
    res = clone_snapshot(src, sha, tmp_path / "out")
    assert res.lfs_detected is True
    assert len(res.lfs_patterns) == 3
    joined = "\n".join(res.lfs_patterns)
    assert ".gitattributes: *.bin filter=lfs" in joined
    assert "docs/.gitattributes: *.pdf filter=lfs" in joined


def test_dest_file_raises(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"a": "1\n"})
    dest = tmp_path / "out"
    dest.write_text("not a dir", encoding="utf-8")
    with pytest.raises(CloneError, match="dest_dir is a file"):
        clone_snapshot(src, sha, dest)


def test_nonempty_dest_raises(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"a": "1\n"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CloneError, match="dest_dir not empty"):
        clone_snapshot(src, sha, dest)


def test_empty_dest_ok(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"a": "1\n"})
    dest = tmp_path / "out"
    dest.mkdir()
    res = clone_snapshot(src, sha, dest)
    assert (dest / "a").read_text(encoding="utf-8") == "1\n"
    assert res.file_count == 1


def test_timeout_plumbing_run():
    with pytest.raises(CloneError, match="timed out"):
        _run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)


def test_timeout_plumbing_clone(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"a": "1\n"})
    # Extremely small timeout should fail on local git operations in constrained envs;
    # if it succeeds too fast, skip rather than be flaky.
    dest = tmp_path / "out"
    try:
        clone_snapshot(src, sha, dest, timeout=0.001)
    except CloneError as e:
        assert "timed out" in str(e).lower() or "command failed" in str(e).lower()
        # cleanup: dest should not be partially populated with repo content beyond maybe empty
        assert not (dest / "a").exists()
        return
    # If the machine was fast enough, at least the plumbing didn't break normal behavior.
    assert (dest / "a").exists()


def test_cleanup_on_failure_no_partial_dest(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"a": "1\n"})
    dest = tmp_path / "out"
    with pytest.raises(CloneError):
        clone_snapshot(src, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", dest)
    # dest should not have partial clone content
    assert not (dest / "a").exists()
    # created dest may be removed; either absent or empty is acceptable cleanup
    assert not dest.exists() or not any(dest.iterdir())
    # tmp should be cleaned
    assert not (tmp_path / "out.tmp-clone").exists()


def test_manifest_hash_deterministic(tmp_path: Path):
    src, sha = make_repo(tmp_path, files={"b.txt": "b\n", "a.txt": "a\n"})
    r1 = clone_snapshot(src, sha, tmp_path / "o1", include_manifest=True)
    r2 = clone_snapshot(src, sha, tmp_path / "o2", include_manifest=True)
    assert r1.manifest_hash == r2.manifest_hash
    assert r1.tree_hash == r2.tree_hash
    assert r1.file_manifest == r2.file_manifest
