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
_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.md$")


def _roots() -> dict[str, Path]:
    return {r["name"]: Path(r["path"]) for r in config.docs_roots}


def _resolve(root: str, doc_name: str) -> Path:
    roots = _roots()
    if root not in roots:
        raise HTTPException(404, f"unknown docs root {root!r}")
    if not _NAME.match(doc_name) or ".." in doc_name:
        raise HTTPException(400, "document names are <name>.md with no path separators")
    base = roots[root].resolve()
    p = (base / doc_name).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404, f"{doc_name} not found under {root}")
    return p


@router.get("/list")
async def list_docs():
    out = []
    for r in config.docs_roots:
        base = Path(r["path"])
        present = base.is_dir()
        docs = sorted(p.name for p in base.glob("*.md")) if present else []
        out.append({"name": r["name"], "path": str(base), "present": present, "documents": docs})
    return {"roots": out}


@router.get("/{root}/{doc_name}")
async def get_doc(root: str, doc_name: str):
    p = _resolve(root, doc_name)
    return {"root": root, "name": doc_name, "content": p.read_text(errors="replace")}


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


@router.get("/{root}/{doc_name}/section/{section_id}")
async def get_section(root: str, doc_name: str, section_id: str):
    text = _resolve(root, doc_name).read_text(errors="replace")
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
