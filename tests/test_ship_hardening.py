import subprocess
from pathlib import Path

import pytest

import codefoundry.ship as ship
from codefoundry.ship import ShipError, ShipResult, ensure_git_repo, ship_private_repo


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def make_repo(tmp: Path, name: str = "src", files: dict[str, str] | None = None) -> Path:
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
    return src


def test_ensure_git_repo_idempotent(tmp_path: Path):
    repo = make_repo(tmp_path, "r1", {"README.md": "hi\n"})
    h1 = ensure_git_repo(repo)
    h2 = ensure_git_repo(repo)
    assert h1 == h2
    assert len(h1) == 40


def test_ensure_git_repo_missing_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r2", {"README.md": "hi\n"})
    monkeypatch.setattr(ship.shutil, "which", lambda cmd: None if cmd == "git" else "/usr/bin/" + cmd)
    with pytest.raises(ShipError) as ei:
        ensure_git_repo(repo)
    assert "git" in str(ei.value).lower()
    assert "preflight" in str(ei.value).lower()


def test_ensure_git_repo_missing_dir(tmp_path: Path):
    with pytest.raises(ShipError) as ei:
        ensure_git_repo(tmp_path / "nope")
    assert "does not exist" in str(ei.value)


@pytest.mark.parametrize(
    "dest",
    [
        "",
        "noslash",
        "a/b/c",
        "/bad",
        "bad/",
        "owner/",
        "/name",
        "owner/../name",
        "owner/name with space",
    ],
)
def test_destination_format_rejected(tmp_path: Path, dest: str):
    repo = make_repo(tmp_path, f"r{abs(hash(dest)) % 10000}", {"README.md": "x\n"})
    with pytest.raises(ShipError) as ei:
        ship_private_repo(repo, destination=dest, dry_run=True)
    msg = str(ei.value)
    assert "destination" in msg.lower() or "owner/name" in msg


def test_private_first_enforcement(tmp_path: Path):
    repo = make_repo(tmp_path, "r3", {"README.md": "x\n"})
    with pytest.raises(ShipError) as ei:
        ship_private_repo(repo, destination="WatchCodes/repo", dry_run=True, visibility="public")
    msg = str(ei.value).lower()
    assert "private" in msg
    assert "--force" not in msg


def test_dry_run_zero_external_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r4", {"README.md": "hi\n"})
    real_run = subprocess.run
    calls: list[list[str]] = []

    def guarded(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        calls.append(cmd_list)
        if cmd_list and cmd_list[0] == "gh":
            raise AssertionError(f"dry-run must not call gh: {cmd_list}")
        if len(cmd_list) >= 2 and cmd_list[0] == "git" and cmd_list[1] == "push":
            raise AssertionError(f"dry-run must not push: {cmd_list}")
        if len(cmd_list) >= 3 and cmd_list[0] == "git" and cmd_list[1] == "remote" and cmd_list[2] in {"add", "set-url"}:
            raise AssertionError(f"dry-run must not mutate remotes: {cmd_list}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", guarded)
    res = ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=True)

    assert res.dry_run is True
    assert res.private is True
    assert res.pushed_sha
    assert len(res.pushed_sha) == 40
    assert res.repo_existed is False
    assert res.remote_updated is False
    assert "origin" not in _git(repo, "remote").stdout.split()
    assert not any(c[0] == "gh" for c in calls)


def test_ship_result_additive_fields_and_to_dict(tmp_path: Path):
    repo = make_repo(tmp_path, "r5", {"README.md": "hi\n"})
    res = ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=True)
    d = res.to_dict()
    for k in ["destination", "repo_url", "private", "pushed_sha", "dry_run", "repo_existed", "remote_updated"]:
        assert k in d
    assert d["dry_run"] is True
    assert d["repo_existed"] is False
    assert d["remote_updated"] is False


def test_non_dry_run_requires_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r6", {"README.md": "hi\n"})
    real_which = ship.shutil.which

    def fake_which(cmd):
        if cmd == "gh":
            return None
        return real_which(cmd)

    monkeypatch.setattr(ship.shutil, "which", fake_which)
    with pytest.raises(ShipError) as ei:
        ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    msg = str(ei.value).lower()
    assert "gh" in msg
    assert "preflight" in msg


def test_existing_repo_idempotent_no_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r7", {"README.md": "hi\n"})
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        calls.append(cmd_list)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            # view destination or isPrivate query - repo exists and is private
            if "--json" in cmd_list:
                return subprocess.CompletedProcess(cmd_list, 0, stdout="true\n", stderr="")
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:3] == ["gh", "repo", "create"]:
            raise AssertionError("should not create when repo exists")
        if cmd_list[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd_list, 0, stdout="pushed\n", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)

    res = ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    assert res.dry_run is False
    assert res.repo_existed is True
    assert not any(c[:3] == ["gh", "repo", "create"] for c in calls)
    assert any(c[:2] == ["git", "push"] for c in calls)


def test_existing_repo_not_private_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r8", {"README.md": "hi\n"})
    real_run = subprocess.run

    def fake(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            if "--json" in cmd_list:
                return subprocess.CompletedProcess(cmd_list, 0, stdout="false\n", stderr="")
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:2] == ["git", "push"]:
            raise AssertionError("must not push to non-private repo")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)

    with pytest.raises(ShipError) as ei:
        ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    msg = str(ei.value).lower()
    assert "private" in msg
    assert "not private" in msg or "refusing" in msg


def test_existing_remote_updated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r9", {"README.md": "hi\n"})
    _git(repo, "remote", "add", "origin", "https://github.com/Old/Other.git")
    real_run = subprocess.run

    def fake(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            if "--json" in cmd_list:
                return subprocess.CompletedProcess(cmd_list, 0, stdout="true\n", stderr="")
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)

    res = ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    assert res.remote_updated is True
    url = _git(repo, "remote", "get-url", "origin").stdout.strip()
    assert url == "https://github.com/WatchCodes/test-repo.git"


def test_push_rejection_has_guidance_no_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r10", {"README.md": "hi\n"})
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        calls.append(cmd_list)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            if "--json" in cmd_list:
                return subprocess.CompletedProcess(cmd_list, 0, stdout="true\n", stderr="")
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(
                cmd_list, 1, stdout="", stderr="! [rejected] HEAD -> main (non-fast-forward)\n"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)

    with pytest.raises(ShipError) as ei:
        ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    msg = str(ei.value)
    assert "push" in msg.lower()
    assert "force" in msg.lower()
    assert "--force" in msg or "no force" in msg.lower()
    # ensure no force push or delete was attempted
    for c in calls:
        joined = " ".join(c)
        assert "--force" not in joined
        assert "-f " not in joined
        assert "--delete" not in joined


def test_gh_create_race_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path, "r11", {"README.md": "hi\n"})
    real_run = subprocess.run
    state = {"view_calls": 0}

    def fake(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        if cmd_list[:3] == ["gh", "repo", "view"]:
            if "--json" in cmd_list:
                return subprocess.CompletedProcess(cmd_list, 0, stdout="true\n", stderr="")
            state["view_calls"] += 1
            # first view: not found, second view (after create fail): found
            if state["view_calls"] == 1:
                return subprocess.CompletedProcess(cmd_list, 1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        if cmd_list[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(cmd_list, 1, stdout="", stderr="already exists")
        if cmd_list[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd_list, 0, stdout="ok\n", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ship.subprocess, "run", fake)
    monkeypatch.setattr(ship.shutil, "which", lambda c: "/usr/bin/" + c)

    res = ship_private_repo(repo, destination="WatchCodes/test-repo", dry_run=False)
    assert res.repo_existed is True
    assert res.dry_run is False


def test_no_force_push_no_delete_in_source():
    # static guard: implementation should not invoke force/delete flags
    src = Path(ship.__file__).read_text(encoding="utf-8")
    guidance_markers = ("do NOT use --force/--delete", "no force-push was attempted")
    filtered_lines = [ln for ln in src.splitlines() if not any(m in ln for m in guidance_markers)]
    filtered = "\n".join(filtered_lines)
    assert "--force" not in filtered
    assert "--delete" not in filtered
    assert " -f " not in filtered
    assert '"-f"' not in filtered
    assert "'-f'" not in filtered
