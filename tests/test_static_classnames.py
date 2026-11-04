"""Class names that ad blockers hide.

Cosmetic filter lists (uBlock, AdBlock, EasyList) hide any element whose class looks like an ad slot.
Josh's Chrome hid `.ad-detail` (the whole ad page rendered as a blank screen) and `.adname`
(ad family names vanished from every table). The rules live in the extension, not in our CSS,
so no test in the app can see them: keep these names out of the Tags UI instead.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
# `ad`, `ads`, `ad-*`, `ads-*`, `adname`, `advert*`, `sponsor*`
BLOCKED = re.compile(r"^(ads?|ad-.*|ads-.*|adname|advert.*|sponsor.*)$")


def _files():
    yield STATIC / "app.css"
    for p in sorted((STATIC / "js").rglob("*.js")):
        if "projection" not in p.relative_to(STATIC / "js").parts:
            yield p


def _tokens(path):
    text = path.read_text(encoding="utf-8")
    toks = set()
    if path.suffix == ".css":
        toks.update(re.findall(r"\.([A-Za-z_][\w-]*)", text))
    else:
        for m in re.finditer(r"class:\s*(?:'([^']*)'|\"([^\"]*)\"|`([^`]*)`)", text):
            body = re.sub(r"\$\{[^}]*\}", " ", m.group(1) or m.group(2) or m.group(3) or "")
            toks.update(t for t in body.split() if t)
        for m in re.finditer(r"className\s*=\s*'([^']*)'", text):
            toks.update(t for t in m.group(1).split() if t)
    return toks


def test_no_ad_blocker_class_names():
    bad = {}
    for path in _files():
        hit = sorted(t for t in _tokens(path) if BLOCKED.match(t))
        if hit:
            bad[str(path.relative_to(STATIC))] = hit
    assert not bad, f"class names that ad blockers hide: {bad}"


if __name__ == "__main__":
    test_no_ad_blocker_class_names()
    print("ok")
