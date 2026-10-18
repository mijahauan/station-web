"""Documentation router: markdown files from the configured docs roots.

Serves ``.md`` files from ``config.docs_roots`` (four fixed roots — see
``station_web.config.DEFAULT_DOCS_ROOTS``), confined to each root's
directory.  Documents may sit in subdirectories (hf-timestd keeps
``design/MEASUREMENT_MODEL.md`` one level down); a document's name is then
its path relative to the root, e.g. ``design/MEASUREMENT_MODEL.md``.  No
evidence/log-fetching: this router never shells out.
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from station_web.config import config

router = APIRouter(prefix="/api/living-docs", tags=["documentation"])
# Bounded so an absurd name can't reach the filesystem as an OSError
# (ENAMETOOLONG) instead of a clean 400; fullmatch (not match) so a
# trailing newline — which "$" alone would let through — is rejected too.
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.md$")
# Each directory component of a nested document's path.  No leading dot, so
# neither ``..`` nor a hidden directory can appear as a component.
_DIR = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")
#: Directory components a document path may carry, and the cap on what one
#: root's listing returns, so a deep or huge tree cannot stall the page.
MAX_DEPTH = 3
MAX_LISTED = 500


def _valid_name(doc_path: str) -> bool:
    """True if ``doc_path`` is ``[dir/]...name.md`` within the bounds above."""
    parts = doc_path.split("/")
    *dirs, leaf = parts
    if len(dirs) > MAX_DEPTH or not _NAME.fullmatch(leaf) or ".." in leaf:
        return False
    return all(_DIR.fullmatch(d) for d in dirs)


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
    if not _valid_name(doc_name):
        raise HTTPException(400, "document names are [dir/]<name>.md (max 128 chars, "
                                 f"at most {MAX_DEPTH} plain directory components)")
    base = roots[root].resolve()
    try:
        p = (base / doc_name).resolve()
    except OSError:
        raise HTTPException(404, f"{doc_name} not found under {root}")
    if not _confined(base, p):
        raise HTTPException(404, f"{doc_name} not found under {root}")
    return p


def _whole_path(request: Request, tail: str) -> None:
    """Refuse a request whose path carries more than the route captured.

    A ``{doc_name:path}`` route compiles to ``^...(.*)$``, and ``$`` matches
    before a trailing newline, so ``GUIDE.md%0A`` would match with the
    newline silently dropped.  The decoded request path must end with
    exactly what the route captured.
    """
    if not request.scope.get("path", "").endswith(tail):
        raise HTTPException(400, "document path carries characters outside the name")


@router.get("/list")
def list_docs():
    out = []
    for r in config.docs_roots:
        base = Path(r["path"])
        present = base.is_dir()
        docs = []
        if present:
            try:
                resolved_base = base.resolve()
                names = sorted(p.relative_to(base).as_posix() for p in base.rglob("*.md"))
            except (OSError, ValueError):
                resolved_base, names = None, []
            for name in names:
                if len(docs) >= MAX_LISTED:
                    break
                if resolved_base is None or not _valid_name(name):
                    continue
                try:
                    resolved = (base / name).resolve()
                except OSError:
                    continue
                if _confined(resolved_base, resolved):
                    docs.append(name)
        out.append({"name": r["name"], "path": str(base), "present": present, "documents": docs})
    return {"roots": out}


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


@router.get("/{root}/{doc_name:path}/section/{section_id}")
def get_section(root: str, doc_name: str, section_id: str, request: Request):
    _whole_path(request, f"/{doc_name}/section/{section_id}")
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


# Registered after the section route: ``{doc_name:path}`` would otherwise
# swallow ``.../section/<id>`` as part of the document path.
@router.get("/{root}/{doc_name:path}")
def get_doc(root: str, doc_name: str, request: Request):
    _whole_path(request, f"/{doc_name}")
    p = _resolve(root, doc_name)
    try:
        content = p.read_text(errors="replace")
    except OSError:
        raise HTTPException(404, f"{doc_name} not found under {root}")
    return {"root": root, "name": doc_name, "content": content}
