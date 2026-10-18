import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"


def test_no_remote_scripts_or_styles():
    offenders = []
    for page in STATIC.glob("*.html"):
        for m in re.finditer(r'<(script|link)[^>]+(src|href)="(https?://[^"]+)"', page.read_text()):
            offenders.append(f"{page.name}: {m.group(3)}")
    assert not offenders, "\n".join(offenders)


def test_vendor_hashes_match_manifest():
    manifest = (STATIC / "vendor" / "VENDOR.md").read_text()
    for name in ("plotly-2.27.0.min.js", "marked.min.js", "topojson/world_110m.json"):
        p = STATIC / "vendor" / name
        assert p.exists(), name
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        assert digest in manifest, f"{name} sha256 {digest} not recorded in VENDOR.md"


def test_cut_pages_and_links_gone():
    assert not (STATIC / "logs.html").exists()
    assert not (STATIC / "timing-validation.html").exists()
    index = (STATIC / "index.html").read_text()
    assert "logs.html" not in index and "timing-validation.html" not in index
