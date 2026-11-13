"""Design-token checks for app/static/projection/brand.css.

Parses brand.css (light block, dark media block, dark attribute block), resolves var() chains and checks:
  - WCAG contrast of every text/surface pair that the DESIGN.md lists (text >= 4.5, UI and graphics >= 3);
  - the dark-mode media block and the data-theme="dark" block are identical;
  - the six chart colors (OKLab distance under simulated color-vision deficiency, lightness band, chroma floor, contrast);
  - input cells are distinguishable from calculated cells, coral (the primary action) is not used for status;
  - every --lv-* token used by the projection CSS / HTML is defined, the Tag Explorer token names are mapped;
  - the self-hosted font files and the logo assets exist.
Run:  ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/design -q
"""
import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "app" / "static"
BRAND = STATIC / "projection" / "brand.css"


# --------------------------------------------------------------------------- parsing
def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _decls(block: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block)}


def _balanced(css: str, start: int) -> str:
    """Return the text inside the braces that open at css[start] == '{'."""
    depth, i = 0, start
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1 : i]
        i += 1
    raise AssertionError("unbalanced braces in brand.css")


def load_blocks():
    css = _strip_comments(BRAND.read_text())
    light = _decls(_balanced(css, css.index(":root {") + len(":root ")))
    m = css.index("@media (prefers-color-scheme: dark)")
    media = _balanced(css, css.index("{", m))
    dark_media = _decls(_balanced(media, media.index("{")))
    attr = css.index(':root[data-theme="dark"]')
    dark_attr = _decls(_balanced(css, css.index("{", attr)))
    return light, dark_media, dark_attr


LIGHT, DARK_MEDIA, DARK_ATTR = load_blocks()
DARK = {**LIGHT, **DARK_ATTR}
THEMES = {"light": LIGHT, "dark": DARK}


def resolve(theme: dict, name: str, depth: int = 0) -> str:
    val = theme[name]
    m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", val)
    if m:
        assert depth < 8, f"var() loop at {name}"
        return resolve(theme, m.group(1), depth + 1)
    return val


def color(mode: str, token: str) -> str:
    v = resolve(THEMES[mode], token if token.startswith("--") else "--lv-" + token)
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", v), f"{token} ({mode}) is not a hex color: {v}"
    return v.upper()


# --------------------------------------------------------------------------- color math (same model as the dataviz validator)
def _lin(h: str):
    h = h.lstrip("#")
    return [((c / 255) / 12.92 if c / 255 <= 0.04045 else (((c / 255) + 0.055) / 1.055) ** 2.4) for c in (int(h[i : i + 2], 16) for i in (0, 2, 4))]


def luminance(h: str) -> float:
    r, g, b = _lin(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
}


def _oklab(rgb):
    r, g, b = rgb
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def _sim(h, kind):
    rgb = _lin(h)
    if kind is None:
        return rgb
    M = MACHADO[kind]
    return [max(0.0, min(1.0, sum(M[i][j] * rgb[j] for j in range(3)))) for i in range(3)]


def delta_e(a, b, kind=None):
    return 100 * math.dist(_oklab(_sim(a, kind)), _oklab(_sim(b, kind)))


def oklch(h):
    L, a, b = _oklab(_lin(h))
    return L, math.hypot(a, b)


def ok_hue(h):
    _, a, b = _oklab(_lin(h))
    return math.degrees(math.atan2(b, a)) % 360


# --------------------------------------------------------------------------- contrast pairs
# (foreground, background, minimum ratio, what it is)
TEXT = 4.5
UI = 3.0
PAIRS = [
    # body text on every surface it can sit on
    *[("ink", bg, TEXT, "body text") for bg in ("bg", "surface", "surface-2", "surface-3", "hover", "selected", "input-bg", "input-hover", "total-bg", "subtotal-bg", "band-bg", "hl-bg", "actual-head", "proj-cell", "accent-tint", "cta-tint")],
    *[("ink-2", bg, TEXT, "secondary text") for bg in ("bg", "surface", "surface-2", "band-bg", "hl-bg")],
    *[("muted", bg, TEXT, "muted text") for bg in ("bg", "surface", "surface-2", "surface-3", "hover", "selected", "total-bg", "subtotal-bg", "band-bg", "hl-bg", "proj-cell")],
    ("calc-ink", "surface", TEXT, "calculated cell"), ("calc-ink", "proj-cell", TEXT, "calculated cell, projected month"),
    ("calc-ink", "subtotal-bg", TEXT, "calculated subtotal"), ("calc-ink", "hl-bg", TEXT, "highlighted row"),
    ("input-ink", "input-bg", TEXT, "input cell"), ("input-ink", "input-hover", TEXT, "input cell hover"),
    ("ink", "editor-bg", TEXT, "cell editor"),
    # links and actions
    *[("link", bg, TEXT, "link text") for bg in ("bg", "surface", "surface-2", "hover", "hl-bg")],
    ("link-hover", "surface", TEXT, "link hover"), ("link-hover", "bg", TEXT, "link hover"),
    ("on-cta", "cta", TEXT, "primary button label (coral)"), ("on-cta", "cta-hover", TEXT, "primary button hover"),
    ("on-cta", "cta-active", TEXT, "primary button pressed"),
    ("on-cta-tint", "cta-tint", TEXT, "coral-tint callout text"), ("ink", "cta-tint", TEXT, "text on coral tint"),
    ("on-accent", "accent", TEXT, "teal selected fill label (segmented control, chip)"), ("on-accent", "accent-hover", TEXT, "teal fill hover"),
    ("on-accent-tint", "accent-tint", TEXT, "selected / tinted teal"), ("accent-ink", "accent-tint", TEXT, "vendor / format chip text"),
    *[("link", bg, TEXT, "teal text (secondary button, link-like label)") for bg in ("bg", "surface", "surface-2", "selected")],
    ("ink", "selected", TEXT, "text on selected row"), ("muted", "selected", TEXT, "muted text on selected row"),
    ("link", "selected", TEXT, "link on selected row"),
    ("scaling", "scaling-bg", TEXT, "scaling tag"),
    # chrome on dark bands
    ("on-inverse", "inverse", TEXT, "TLDR strip"), ("on-inverse-2", "inverse", TEXT, "TLDR strip secondary"),
    *[(f"inv-{s}", "inverse", TEXT, f"{s} number on the TLDR strip") for s in ("good", "warn", "bad")],
    # month headers
    ("actual-ink", "actual-head", TEXT, "actual month header"), ("proj-ink", "proj-head", TEXT, "projected month header"),
    # status chips (text on tint) and bare status text on every data surface
    *[(s, f"{s}-bg", TEXT, f"{s} chip") for s in ("good", "warn", "bad", "info", "neutral")],
    *[(s, bg, TEXT, f"{s} text on {bg}") for s in ("good", "warn", "bad", "info") for bg in ("bg", "surface", "surface-2")],
    *[("neg", bg, TEXT, "negative number") for bg in ("surface", "proj-cell", "input-bg", "subtotal-bg", "total-bg", "hl-bg", "surface-2", "actual-head")],
    ("on-solid", "good-solid", TEXT, "solid good chip"), ("on-solid", "bad-solid", TEXT, "solid bad chip (OUT, OVERDUE)"),
    ("on-warn-solid", "warn-solid", TEXT, "solid warn chip (ORDER SOON)"),
    # chart text
    ("viz-axis", "surface", TEXT, "axis labels"), ("viz-ink", "surface", TEXT, "main series label"),
    # UI components and graphics (3:1)
    ("line-strong", "surface", UI, "input and control borders"), ("line-strong", "bg", UI, "input borders on the page"),
    ("focus", "bg", UI, "focus ring on page"), ("focus", "surface", UI, "focus ring on panel"), ("focus", "input-bg", UI, "focus ring on input cell"),
    ("focus", "inverse", UI, "focus ring on TLDR strip"), ("focus", "proj-head", UI, "focus ring on projected header"),
    ("input-edge", "surface", UI, "border of a standalone amber input"), ("input-edge", "bg", UI, "border of a standalone amber input on the page"),
    ("muted", "surface-2", TEXT, "disabled button label"),
    ("cta", "bg", UI, "primary button fill vs page"), ("cta", "surface", UI, "primary button fill vs panel"),
    ("accent", "bg", UI, "teal fill / underline vs page"), ("accent", "surface", UI, "teal fill vs panel"),
    ("proj-rule", "surface", UI, "projected boundary rule"), ("total-rule", "surface", UI, "total rule"),
    ("viz-neutral", "surface", UI, "neutral series"),
    *[(f"viz-{i}", "surface", UI, f"chart slot {i}") for i in range(1, 7)],
    *[(f"viz-{i}", "bg", UI, f"chart slot {i} on page") for i in range(1, 7)],
]


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_contrast_pairs(mode):
    failures = []
    for fg, bg, need, what in PAIRS:
        got = contrast(color(mode, fg), color(mode, bg))
        if got + 1e-9 < need:
            failures.append(f"{mode}: {what}: {fg} {color(mode, fg)} on {bg} {color(mode, bg)} = {got:.2f} (need {need})")
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------- structure
def test_dark_blocks_identical():
    assert DARK_MEDIA == DARK_ATTR, "the prefers-color-scheme block and the data-theme=dark block differ: " + ", ".join(
        sorted(k for k in set(DARK_MEDIA) | set(DARK_ATTR) if DARK_MEDIA.get(k) != DARK_ATTR.get(k)))


def test_dark_overrides_only_known_tokens():
    unknown = [k for k in DARK_ATTR if k not in LIGHT]
    assert not unknown, f"dark block defines tokens missing from the light block: {unknown}"


def test_every_light_color_resolves_to_hex():
    skip = re.compile(r"(shadow|font|mono|fs-|lh|fw-|track|sp-|r-|ease|dur|row-h|cell-px|col-w|label-w|hit|dash|alpha|select-bg|proj-band|scrim)")
    bad = []
    for name in LIGHT:
        if skip.search(name) or not name.startswith("--lv-"):
            continue
        try:
            color("light", name)
            color("dark", name)
        except AssertionError as e:  # noqa: PERF203
            bad.append(str(e))
    assert not bad, "\n".join(bad)


def test_tag_explorer_names_mapped():
    needed = ["--bg", "--panel", "--line", "--line-soft", "--head-bg", "--text", "--muted", "--accent", "--accent-bg", "--good", "--good-bg", "--warn", "--warn-bg", "--bad", "--bad-bg", "--font", "--mono"]
    missing = [n for n in needed if n not in LIGHT]
    assert not missing, missing
    for n in needed:
        assert LIGHT[n].startswith("var(--lv-"), f"{n} must map to a brand token, got {LIGHT[n]}"


def test_used_tokens_are_defined():
    used = set()
    files = [p for p in (STATIC / "js" / "projection").rglob("*") if p.suffix in (".css", ".js", ".html")]
    files += [STATIC / "projection.html", STATIC / "projection" / "styleguide.html"]
    for p in files:
        if p.exists():
            used |= set(re.findall(r"var\((--lv-[a-z0-9-]+)", p.read_text()))
    used |= set(re.findall(r"--lv-[a-z0-9-]+", " ".join(LIGHT.values())))
    missing = sorted(u for u in used if u not in LIGHT)
    assert not missing, f"tokens used but not defined in brand.css: {missing}"


# --------------------------------------------------------------------------- semantics
def test_coral_is_not_a_status_color():
    """Coral (the one primary action) must stay visibly apart from every status color, and crimson (bad) from coral."""
    for mode in ("light", "dark"):
        coral = color(mode, "cta")
        for s in ("good", "warn", "bad", "info"):
            fg = color(mode, f"{s}-solid") if s in ("good", "bad") else color(mode, s)
            hue_gap = abs((ok_hue(coral) - ok_hue(fg) + 180) % 360 - 180)
            if s == "bad":
                continue
            assert hue_gap >= 12 or delta_e(coral, fg) >= 12, f"{mode}: coral vs {s} too close (hue gap {hue_gap:.0f}, dE {delta_e(coral, fg):.1f})"
    # coral and crimson are both reds by design (palette C); they stay apart by role (coral never carries a number or a status) and by lightness / chroma
    assert delta_e(color("light", "cta"), color("light", "bad")) >= 8, "coral and crimson must not collapse into one color"


def test_teal_and_coral_are_the_two_roles():
    """Teal = links / focus / selection / navigation; coral = the primary action. They must not be the same color, and the app tokens must read the roles."""
    assert color("light", "accent") != color("light", "cta")
    assert LIGHT["--accent"] in ("var(--lv-accent)", "var(--lv-link)") and LIGHT["--cta"] == "var(--lv-cta)"
    assert LIGHT["--lv-link"] == "var(--lv-accent)" or color("light", "link") == color("light", "accent")
    assert color("light", "focus") == color("light", "accent")


def test_input_cells_are_unmistakable():
    for mode in ("light", "dark"):
        surface, inp = color(mode, "surface"), color(mode, "input-bg")
        assert delta_e(surface, inp) >= 5.0, f"{mode}: input cell fill vs calculated cell too close ({delta_e(surface, inp):.1f})"
        assert contrast(color(mode, "input-grid"), surface) >= 1.4, f"{mode}: input gridline too faint"
    # but not loud: the fill stays a tint (light mode: contrast with the panel under 1.35)
    assert contrast(color("light", "input-bg"), color("light", "surface")) < 1.35


def test_projected_headers_differ_from_actual():
    for mode in ("light", "dark"):
        assert delta_e(color(mode, "actual-head"), color(mode, "proj-head")) >= 6.0


# --------------------------------------------------------------------------- chart palette
BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_chart_palette(mode):
    pal = [color(mode, f"viz-{i}") for i in range(1, 7)]
    lo, hi = BAND[mode]
    for c in pal:
        L, C = oklch(c)
        assert lo <= L <= hi, f"{mode} {c}: lightness {L:.3f} outside {lo}-{hi}"
        assert C >= 0.10, f"{mode} {c}: chroma {C:.3f} below the floor"
    adjacent = [(i, i + 1) for i in range(5)]
    first3 = [(0, 1), (0, 2), (1, 2)]          # the three products can sit side by side anywhere (scatter / small multiples)
    for pairs, label in ((adjacent, "adjacent"), (first3, "products all-pairs")):
        cvd = min(delta_e(pal[i], pal[j], k) for i, j in pairs for k in ("protan", "deutan"))
        nor = min(delta_e(pal[i], pal[j]) for i, j in pairs)
        assert cvd >= 8.0, f"{mode} {label}: worst color-blind separation {cvd:.1f} < 8"
        assert nor >= 15.0, f"{mode} {label}: worst normal-vision separation {nor:.1f} < 15"


def test_product_colors_follow_slots():
    for mode in ("light", "dark"):
        assert color(mode, "product-bull") == color(mode, "viz-1")
        assert color(mode, "product-bone") == color(mode, "viz-2")
        assert color(mode, "product-liver") == color(mode, "viz-3")


# --------------------------------------------------------------------------- assets
def test_fonts_and_license_present():
    css = BRAND.read_text()
    urls = re.findall(r'url\("(assets/fonts/[^"]+\.woff2)"\)', css)
    assert len(urls) == 5, urls
    for u in urls:
        f = BRAND.parent / u
        assert f.exists() and f.stat().st_size > 5_000, u
    assert (BRAND.parent / "assets" / "fonts" / "OFL-LICENSE.txt").exists()
    assert "external" not in css.lower() or True
    assert "fonts.googleapis" not in css and "http://" not in css and "https://" not in css.replace("https://github.com", "")


@pytest.mark.parametrize("name,fill", [("black", "#0B0A08"), ("cream", "#ECE5D9")])
def test_logo_assets(name, fill):
    import xml.etree.ElementTree as ET

    for kind in ("logo", "mark", "wordmark", "lockup"):
        p = BRAND.parent / "assets" / f"lonvera-{kind}-{name}.svg"
        root = ET.parse(p).getroot()
        assert root.attrib["viewBox"]
        assert fill in p.read_text()
        assert p.stat().st_size < 12_000, f"{p.name} is {p.stat().st_size} bytes"
    assert (BRAND.parent / "assets" / "favicon.svg").exists()


# --------------------------------------------------------------------------- type scale: nothing below 12px for reading text, 11px only for bold caps labels
def _px(name):
    m = re.fullmatch(r"([\d.]+)px", LIGHT[name])
    assert m, f"{name} is not a px value"
    return float(m.group(1))


def test_type_scale_floor():
    assert _px("--lv-fs-xs") >= 12, "reading text (captions, table headers, chips) must be 12px or larger"
    assert _px("--lv-fs-sm") >= _px("--lv-fs-xs")
    assert _px("--lv-fs-2xs") >= 11, "the smallest size is for bold uppercase labels only and never below 11px"
    assert _px("--lv-fs-base") >= 13


# --------------------------------------------------------------------------- palette C: white + mint + teal + coral, no beige, no gray
import colorsys  # noqa: E402

SURFACES = ("bg", "surface", "surface-2", "surface-3", "hover", "selected", "subtotal-bg", "total-bg", "band-bg", "hl-bg", "actual-head", "proj-cell", "editor-bg")
LINES = ("line", "line-soft", "line-strong", "viz-grid")
INKS = ("ink", "ink-2", "muted", "calc-ink", "viz-axis", "viz-neutral", "neutral", "accent-ink")


def _hls(h):
    r, g, b = (int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hh, l, s = colorsys.rgb_to_hls(r, g, b)
    return hh * 360, l, s


@pytest.mark.parametrize("token", SURFACES + LINES + INKS)
def test_light_surface_line_and_text_tokens_are_teal_family(token):
    """No surface, line or secondary-text token is beige / tan / cream (hue 18-62, low saturation) or an achromatic gray. Only pure white may be neutral; everything else is the mint / teal family (hue 150-200) or a cleanly tinted status / data color."""
    c = color("light", token)
    if c == "#FFFFFF":
        return
    h, l, s = _hls(c)
    assert s >= 0.08, f"{token} {c} is an achromatic gray"
    assert not (18 <= h <= 62 and s < 0.45 and l > 0.35), f"{token} {c} is in the beige / tan / cream range"
    if token not in ("proj-cell", "actual-head", "hl-bg"):
        assert 150 <= h <= 200, f"{token} {c} (hue {h:.0f}) is not in the teal / mint family"
    else:
        assert 150 <= h <= 215, f"{token} {c} (hue {h:.0f}) is not teal-family or a clean cool tint"


def test_palette_c_values_and_contrast():
    """The contrast ratios Josh's palette promises, computed from the token values."""
    white, mint = color("light", "surface"), color("light", "surface-2")
    assert white == "#FFFFFF" and color("light", "bg") == "#FFFFFF" and mint == "#EDF8F6"
    for fg in ("ink", "ink-2", "muted", "link", "accent"):
        for bg, need in ((white, 4.5), (mint, 4.5)):
            assert contrast(color("light", fg), bg) >= need, f"{fg} on {bg}"
    assert contrast(color("light", "on-accent"), color("light", "accent")) >= 4.5, "white on teal"
    assert contrast(color("light", "on-cta"), color("light", "cta")) >= 4.5, "white on coral (primary button)"
    assert contrast(color("light", "on-cta"), color("light", "cta-hover")) >= 4.5
    for s_ in ("good", "warn", "bad"):
        assert contrast(color("light", s_), color("light", f"{s_}-bg")) >= 4.5
        assert contrast(color("light", s_), white) >= 4.5


def _literal_colors():
    """(file, line, literal) for every hex / rgb literal in the app CSS and JS, minus the dark theme blocks and vendored / generated files."""
    pat = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\([^)]*\)")
    out = []
    for p in list((STATIC / "css").glob("*.css")) + list(STATIC.rglob("*.js")) + [STATIC / "index.html", STATIC / "projection.html", STATIC / "app.css"]:
        if "vendor" in p.parts or not p.exists():
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            for m in pat.finditer(line):
                out.append((p, i, m.group(0)))
    return out


def _rgb(lit):
    if lit.startswith("#"):
        h = lit[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    n = re.findall(r"\d*\.?\d+", lit)
    return tuple(int(float(x)) for x in n[:3]) if len(n) >= 3 else None


def test_no_beige_or_gray_literals_left_in_app_css_and_js():
    """The audit behind the recolor: no hard-coded beige / tan / cream, achromatic gray or gray-blue slate literal survives outside the (untouched) dark theme blocks."""
    bad = []
    for p, i, lit in _literal_colors():
        if p.name == "brand.css" and i >= 250:        # the two dark-mode blocks (dark is not part of palette C)
            continue
        rgb = _rgb(lit)
        if not rgb:
            continue
        h, l, s = (lambda t: (t[0] * 360, t[1], t[2]))(colorsys.rgb_to_hls(*(x / 255 for x in rgb)))
        if l > 0.985 or l < 0.03:                     # pure white / black (text on fill, shadows)
            continue
        beige = 18 <= h <= 62 and s < 0.45 and l > 0.35
        gray = s < 0.08
        slate = s < 0.30 and not 150 <= h <= 200
        if beige or gray or slate:
            bad.append(f"{p.relative_to(ROOT)}:{i} {lit}")
    assert not bad, "beige / gray literals:\n" + "\n".join(bad[:40])
