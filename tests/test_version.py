"""Git provenance, read from .git's on-disk metadata — no subprocess."""
from __future__ import annotations

from pathlib import Path

from station_web.version import detect_git_info


def _make_repo(tmp_path: Path, *, sha: str, branch: str | None) -> Path:
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    if branch is None:
        (git_dir / "HEAD").write_text(sha + "\n")
    else:
        (git_dir / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
        (git_dir / "refs" / "heads" / branch).write_text(sha + "\n")
    return tmp_path


def test_symbolic_head_reads_branch_and_sha(tmp_path):
    sha = "abc123def4567890abc123def4567890abc123d"
    repo = _make_repo(tmp_path, sha=sha, branch="main")
    info = detect_git_info(repo)
    assert info["sha"] == sha
    assert info["short"] == sha[:7]
    assert info["ref"] == "main"


def test_detached_head_yields_ref_detached(tmp_path):
    sha = "0123456789abcdef0123456789abcdef01234567"
    repo = _make_repo(tmp_path, sha=sha, branch=None)
    info = detect_git_info(repo)
    assert info["sha"] == sha
    assert info["short"] == sha[:7]
    assert info["ref"] == "detached"


def test_no_git_dir_above_yields_empty(tmp_path):
    assert detect_git_info(tmp_path) == {}
