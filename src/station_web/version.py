"""Git provenance for inventory --json output.

Same shape and detection as meteor-scatter / psk-recorder: it lets
``smd admin diag`` answer "what's running?" without shelling into the
client.  Absent git (a wheel install, a tarball) yields ``{}`` and the
caller omits the block rather than emitting empty strings.

CLIENT-CONTRACT forbids shelling out to git from a client's contract
surface (a hygiene rule Task 6 enforces), so provenance is read directly
from ``.git``'s on-disk metadata — ``HEAD``, ``refs/heads/<branch>``,
``packed-refs`` — rather than by invoking the ``git`` binary. This costs
the ``dirty`` flag (unknowable without running ``git status``); the
contract's ``git`` block is optional and a partial block is acceptable.
"""

from __future__ import annotations

from pathlib import Path


def _find_git_dir(start: Path) -> Path | None:
    """Walk ``start`` and its parents for the first ``.git`` — a directory,
    or (worktrees) a file containing ``gitdir: <path>``."""
    for d in (start, *start.parents):
        candidate = d / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            try:
                text = candidate.read_text().strip()
            except OSError:
                continue
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (d / gitdir).resolve()
                if gitdir.is_dir():
                    return gitdir
    return None


def _read_branch_sha(git_dir: Path, ref_name: str) -> str:
    ref_file = git_dir / "refs" / "heads" / ref_name
    if ref_file.is_file():
        return ref_file.read_text().strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        target = f"refs/heads/{ref_name}"
        for line in packed.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == target:
                return parts[0]
    return ""


def detect_git_info(start: Path) -> dict:
    """Read git provenance for the repo containing ``start``, pure Python.

    Returns ``{}`` on any missing/unparsable metadata (no ``.git``, no
    ``HEAD``, a branch ref that resolves to nothing) or on ``OSError``.
    """
    try:
        git_dir = _find_git_dir(start)
        if git_dir is None:
            return {}
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return {}
        head = head_file.read_text().strip()
        if head.startswith("ref:"):
            ref_path = head.split(":", 1)[1].strip()
            prefix = "refs/heads/"
            ref_name = ref_path[len(prefix):] if ref_path.startswith(prefix) else ref_path
            sha = _read_branch_sha(git_dir, ref_name)
            if not sha:
                return {}
            return {"sha": sha, "short": sha[:7], "ref": ref_name}
        sha = head
        if not sha:
            return {}
        return {"sha": sha, "short": sha[:7], "ref": "detached"}
    except OSError:
        return {}


GIT_INFO: dict = detect_git_info(Path(__file__).resolve())
