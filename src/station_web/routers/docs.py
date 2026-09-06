"""Documentation router: markdown files from the configured docs roots.

Serves ``.md`` files from ``config.docs_roots`` (four fixed roots — see
``station_web.config.DEFAULT_DOCS_ROOTS``), confined to each root's
directory.  No evidence/log-fetching: this router never shells out.
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from station_web.config import config

router = APIRouter(prefix="/api/living-docs", tags=["documentation"])
# Bounded so an absurd name can't reach the filesystem as an OSError
# (ENAMETOOLONG) instead of a clean 400; fullmatch (not match) so a
# trailing newline — which "$" alone would let through — is rejected too.
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.md$")


def _roots() -> dict[str, Path]:
    return {r["name"]: Path(r["path"]) for r in config.docs_roots}


def _confined(base: Path, p: Path) -> bool:
    """True if the already-``resolve()``d path ``p`` is a regular file strictly under the
    already-``resolve()``d ``base`` — i.e. no symlink or ``..`` escaped the root."""
    try:
        return base in p.parents and p.is_file()
    except OSError:
        return False


def _resolve(root: str, doc_name: str) -> Path:
    roots = _roots()
    if root not in roots:
        raise HTTPException(404, f"unknown docs root {root!r}")
    if not _NAME.fullmatch(doc_name) or ".." in doc_name:
        raise HTTPException(400, "document names are <name>.md (max 128 chars) with no path separators")
    base = roots[root].resolve()
    try:
        p = (base / doc_name).resolve()
    except OSError:
        raise HTTPException(404, f"{doc_name} not found under {root}")
    if not _confined(base, p):
        raise HTTPException(404, f"{doc_name} not found under {root}")
    return p


@router.get("/list")
async def list_docs():
    out = []
    for r in config.docs_roots:
        base = Path(r["path"])
        present = base.is_dir()
        docs = []
        if present:
            try:
                resolved_base = base.resolve()
                names = sorted(p.name for p in base.glob("*.md"))
            except OSError:
                resolved_base, names = None, []
            for name in names:
                if resolved_base is None or not _NAME.fullmatch(name):
                    continue
                try:
                    resolved = (base / name).resolve()
                except OSError:
                    continue
                if _confined(resolved_base, resolved):
                    docs.append(name)
        out.append({"name": r["name"], "path": str(base), "present": present, "documents": docs})
    return {"roots": out}


@router.get("/{root}/{doc_name}")
async def get_doc(root: str, doc_name: str):
    p = _resolve(root, doc_name)
    try:
        content = p.read_text(errors="replace")
    except OSError:
        raise HTTPException(404, f"{doc_name} not found under {root}")
    return {"root": root, "name": doc_name, "content": content}


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


@router.get("/{root}/{doc_name}/section/{section_id}")
async def get_section(root: str, doc_name: str, section_id: str):
    p = _resolve(root, doc_name)
    try:
        text = p.read_text(errors="replace")
    except OSError:
        raise HTTPException(404, f"{doc_name} not found under {root}")
    lines = text.splitlines()
    start = None
    level = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m and _slug(m.group(2)) == section_id:
            start, level = i, len(m.group(1))
            break
    if start is None:
        raise HTTPException(404, f"section {section_id!r} not found")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return {"root": root, "name": doc_name, "section": section_id, "content": "\n".join(lines[start:end])}
