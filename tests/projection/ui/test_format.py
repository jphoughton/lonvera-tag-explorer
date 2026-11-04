"""lib/format.js: parsing, display, status tones. Runs in the browser against the real module."""
import pytest

CASES = [
    # text, fmt, expected value | None for blank | "ERR"
    ("$1,234.50", "usd", 1234.5), ("1,234", "usd0", 1234), ("(1,234)", "usd", -1234), ("-5", "num", -5), ("$-5", "usd", -5),
    ("12%", "pct", 0.12), ("12", "pct", 0.12), ("0.5", "pct", 0.005), ("12.5%", "usd", 0.125),
    ("1.2x", "x", 1.2), ("1.2", "x", 1.2), ("350k", "usd0", 350000), ("1.5m", "usd0", 1500000), ("$2.5M", "usd0", 2500000), ("1b", "usd0", 1e9),
    ("", "usd", None), ("-", "usd", None), ("—", "usd", None), ("  ", "num", None),
    ("abc", "usd", "ERR"), ("12kk", "usd", "ERR"), ("1.2.3", "num", "ERR"), ("$", "usd", "ERR"),
    ("2026-11-05", "date", "2026-11-05"), ("11/5/2026", "date", "2026-11-05"), ("2/30/2026", "date", "ERR"),
]


@pytest.mark.parametrize("text,fmt,expected", CASES)
def test_parse(page, text, fmt, expected):
    page.goto("/projection")
    out = page.evaluate("async ([t, f]) => { const m = await import('/static/js/projection/lib/format.js'); return m.parse(t, f); }", [text, fmt])
    if expected == "ERR":
        assert out["ok"] is False
    else:
        assert out["ok"] is True
        if isinstance(expected, float):
            assert out["value"] == pytest.approx(expected)
        else:
            assert out["value"] == expected


DISPLAY = [
    (1234567.4, "usd0", "$1,234,567"),
    (-1234.5, "usd0", "($1,235)"), (-1234.5, "usd", "($1,234.50)"), (0, "usd0", "—"), (None, "usd0", ""),
    (0.1234, "pct", "12.3%"), (-0.05, "pct", "-5.0%"), (1.2, "x", "1.20x"), (12345, "num", "12,345"), (-12, "num", "(12)"), (2.5, "num", "2.50"),
    ("2026-11-05", "date", "Nov 5, 2026"), ("OK", "text", "OK"),
]


@pytest.mark.parametrize("v,fmt,expected", DISPLAY)
def test_display(page, v, fmt, expected):
    page.goto("/projection")
    out = page.evaluate("async ([v, f]) => { const m = await import('/static/js/projection/lib/format.js'); return m.display(v, f).text; }", [v, fmt])
    assert out == expected


def test_labels_compact_tones(page):
    page.goto("/projection")
    out = page.evaluate("""async () => { const m = await import('/static/js/projection/lib/format.js');
      return { ml: m.monthLabel('2026-11-01'), ml2: m.monthLabel('2026-11-01', true), c1: m.compact(1234567), c2: m.compact(-350000), c3: m.compact(2100000000),
               t1: m.statusTone('OUT'), t2: m.statusTone('ORDER SOON'), t3: m.statusTone('ok'), t4: m.statusTone('Hello') }; }""")
    assert out == {"ml": "Nov 2026", "ml2": "November 2026", "c1": "$1.2M", "c2": "-$350K", "c3": "$2.1B", "t1": "bad", "t2": "warn", "t3": "good", "t4": None}
