"""Helpers shared by the pnl tests: a tiny .xlsx writer and a QuickBooks-style export builder."""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def col_letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(rows: list[list], *, extra_member: tuple[str, bytes] | None = None, doctype: bool = False) -> bytes:
    """A minimal valid workbook with one sheet; strings are shared strings, numbers numeric."""
    shared: list[str] = []
    sheet_rows = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            if v is None:
                continue
            ref = f"{col_letters(ci)}{ri}"
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                if str(v) not in shared:
                    shared.append(str(v))
                cells.append(f'<c r="{ref}" t="s"><v>{shared.index(str(v))}</v></c>')
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = f'<?xml version="1.0"?>{"<!DOCTYPE x [<!ENTITY a \"b\">]>" if doctype else ""}<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    sst = f'<?xml version="1.0"?><sst {ns}>{"".join(f"<si><t>{escape(s)}</t></si>" for s in shared)}</sst>'
    wb = (f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Profit and Loss" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", sst)
        if extra_member:
            z.writestr(*extra_member)
    return buf.getvalue()


MONTH_LABELS = ["Jan 2026", "Feb 2026", "Mar 2026"]


def qb_rows(months=MONTH_LABELS) -> list[list]:
    """A QuickBooks 'Profit and Loss by Month' layout (title block, header row, sections, totals)."""
    n = len(months)
    def line(name, vals):
        return [name, *vals, sum(v for v in vals if isinstance(v, (int, float)))]
    return [
        ["Lonvera LLC"], ["Profit and Loss"], ["January - March 2026"], [],
        ["", *months, "Total"],
        ["Income"],
        line("Shopify Sales", [1000.0, 2000.0, 3000.0][:n]),
        line("Amazon Sales", [400.0, 500.0, 600.0][:n]),
        line("Total Income", [1400.0, 2500.0, 3600.0][:n]),
        ["Cost of Goods Sold"],
        line("Amazon FBA Fees", [100.0, 110.0, 120.0][:n]),
        line("China COGS", [200.0, 210.0, 220.0][:n]),
        line("Fulfillment Services", [90.0, 95.0, 99.0][:n]),
        line("Total Cost of Goods Sold", [390.0, 415.0, 439.0][:n]),
        line("Gross Profit", [1100.0, 2180.0, 3260.0][:n]),
        ["Expenses"],
        line("Facebook Ads", [300.0, 310.0, 320.0][:n]),
        ["Other Business Expenses"],
        line("Other Business Expenses", [5.0, 6.0, 7.0][:n]),
        line("Bank Fees", [1.0, 2.0, 3.0][:n]),
        line("Total for Other Business Expenses", [6.0, 8.0, 10.0][:n]),
        line("Accounting", [50.0, 60.0, 70.0][:n]),
        line("Total for Expenses", [56.0, 68.0, 80.0][:n]),
        line("Net Operating Income", [1044.0, 2112.0, 3180.0][:n]),
    ]


def to_csv(rows: list[list]) -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode()


# ---------------------------------------------------------------------------------------------------------------- new chart (qb2026) exports
# Synthetic numbers (not Josh's books) in the layout of the QuickBooks "Profit and Loss" with numbered accounts: header accounts with no amount,
# "Total for ..." rows, computed rows, and for a single month a "% of Income" column.
QB26_ACCOUNTS: list[tuple[str, float]] = [
    ("4010 Amazon Sales", 100_000.0), ("4020 DTC Sales", 800_000.0), ("4030 TikTok Sales", 1_000.0), ("4200 Shipping Income", 10_000.0),
    ("4400 Refunds & Discounts to Customers", -150_000.0),
    ("5010 Amazon COGS", 10_000.0), ("5020 Shopify COGS", 80_000.0), ("5030 TikTok COGS", 100.0),
    ("5310 3PL Services", 90_000.0), ("5320 FBA Fees", 12_000.0),
    ("5410 Adyen Fees", 25_000.0), ("5420 Amazon Fees", 18_000.0), ("5430 PayPal Fees", 12_000.0), ("5440 Shopify Fees", 9_000.0), ("5450 TikTok Fees", 50.0),
    ("6011 Creative Agency", 30_000.0), ("6012 Email Agency", 8_000.0),
    ("6031 Amazon Ads", 20_000.0), ("6032 Facebook Ads", 300_000.0), ("6033 Google Ads", 10_000.0), ("6035 TikTok Ads", 50.0),
    ("6110 Accounting", 2_500.0), ("6115 Chargeback Management", 500.0), ("6120 Consulting", 12_000.0), ("6125 Customer Service", 8_000.0),
    ("6135 Legal", 3_000.0), ("6145 Office Supplies", 2_000.0), ("6166 Marketing", 7_000.0), ("6167 Operations", 5_000.0), ("6170 Bank Fees", 400.0),
    ("7000 Other Income", 300.0), ("8000 Interest Expense", 30_000.0),
]


def qb26_expected(factor: float = 1.0) -> dict[str, float]:
    a = {name.split(" ", 1)[0]: v * factor for name, v in QB26_ACCOUNTS}
    gross = a["4010"] + a["4020"] + a["4030"]
    total_income = gross + a["4200"] + a["4400"]
    cogs = a["5010"] + a["5020"] + a["5030"] + a["5310"] + a["5320"] + a["5410"] + a["5420"] + a["5430"] + a["5440"] + a["5450"]
    marketing = a["6011"] + a["6012"] + a["6031"] + a["6032"] + a["6033"] + a["6035"]
    overhead = a["6110"] + a["6115"] + a["6120"] + a["6125"] + a["6135"] + a["6145"] + a["6166"] + a["6167"] + a["6170"]
    noi = total_income - cogs - marketing - overhead
    other = a["7000"] - a["8000"]
    return {"gross_sales": gross, "total_income": total_income, "total_cogs": cogs, "gross_profit": total_income - cogs, "marketing_total": marketing,
            "overhead_total": overhead, "total_expenses": marketing + overhead, "noi": noi, "net_other_income": other, "net_income": noi + other}


def qb26_rows(labels: list[str], factors: list[float] | None = None, *, drop_codes: bool = False) -> list[list]:
    """A new-chart export. One label = the single-month layout (Total / month header, '% of Income' column); several = month per column plus a Total column.
    `factors` scale each month's amounts. `drop_codes` writes bare account names (the two Software children then collide: they are told apart by their parent)."""
    factors = factors or [1.0] * len(labels)
    single = len(labels) == 1

    def name(n: str) -> str:
        return n.split(" ", 1)[1] if drop_codes else n

    def line(n: str, vals: list[float | None]):
        if single:
            return [n, vals[0], 0.0 if vals[0] is None else 1.5]
        return [n, *vals, sum(v for v in vals if v is not None)]

    def acct(code: str):
        full = next(n for n, _ in QB26_ACCOUNTS if n.startswith(code))
        base = dict(QB26_ACCOUNTS)[full]
        return line(name(full), [round(base * f, 2) for f in factors])

    def hdr(n: str):
        return line(n, [None] * len(labels))

    def tot(n: str, vals: list[float]):
        return line(n, [round(v, 2) for v in vals])

    ex = [qb26_expected(f) for f in factors]
    head = [["TEST CO LLC"], ["Profit and Loss"], [labels[0] if single else f"{labels[0]} - {labels[-1]}"], []]
    if single:
        head += [[None, "Total"], [None, labels[0], "% of Income"]]
    else:
        head += [[None, *labels, "Total"]]
    rows = head + [["Income"], hdr("4000 Sales"), acct("4010"), acct("4020"), acct("4030"), tot("Total for 4000 Sales", [e["gross_sales"] for e in ex]),
                   acct("4200"), acct("4400"), tot("Total Income", [e["total_income"] for e in ex]),
                   ["Cost of Goods Sold"], hdr("5000 Cost of Goods Sold"), acct("5010"), acct("5020"), acct("5030"),
                   tot("Total for 5000 Cost of Goods Sold", [qb26_expected_part(f, ("5010", "5020", "5030")) for f in factors]),
                   hdr("5300 Fulfillment"), acct("5310"), acct("5320"), tot("Total for 5300 Fulfillment", [qb26_expected_part(f, ("5310", "5320")) for f in factors]),
                   hdr("5400 Seller Fees"), acct("5410"), acct("5420"), acct("5430"), acct("5440"), acct("5450"),
                   tot("Total for 5400 Seller Fees", [qb26_expected_part(f, ("5410", "5420", "5430", "5440", "5450")) for f in factors]),
                   tot("Total Cost of Goods Sold", [e["total_cogs"] for e in ex]), tot("Gross Profit", [e["gross_profit"] for e in ex]),
                   ["Expenses"], hdr("6000 Marketing"), hdr("6010 Marketing Agency"), acct("6011"), acct("6012"),
                   tot("Total for 6010 Marketing Agency", [qb26_expected_part(f, ("6011", "6012")) for f in factors]),
                   hdr("6030 Marketing Media"), acct("6031"), acct("6032"), acct("6033"), acct("6035"),
                   tot("Total for 6030 Marketing Media", [qb26_expected_part(f, ("6031", "6032", "6033", "6035")) for f in factors]),
                   tot("Total for 6000 Marketing", [e["marketing_total"] for e in ex]),
                   hdr("6100 Overhead Expenses"), acct("6110"), acct("6115"), acct("6120"), acct("6125"), acct("6135"), acct("6145"),
                   hdr("6165 Software & Apps"), acct("6166"), acct("6167"),
                   tot("Total for 6165 Software & Apps", [qb26_expected_part(f, ("6166", "6167")) for f in factors]),
                   acct("6170"), tot("Total for 6100 Overhead Expenses", [e["overhead_total"] for e in ex]),
                   tot("Total Expenses", [e["total_expenses"] for e in ex]), tot("Net Operating Income", [e["noi"] for e in ex]),
                   ["Other Income"], acct("7000"), tot("Total Other Income", [qb26_expected_part(f, ("7000",)) for f in factors]),
                   ["Other Expenses"], hdr("Unrealized Gain or Loss"), acct("8000"), tot("Total Other Expenses", [qb26_expected_part(f, ("8000",)) for f in factors]),
                   tot("Net Other Income", [e["net_other_income"] for e in ex]), tot("Net Income", [e["net_income"] for e in ex]), [], [],
                   ["Accrual Basis Tuesday, November 24, 2026"]]
    return rows


def qb26_expected_part(factor: float, codes: tuple[str, ...]) -> float:
    d = dict(QB26_ACCOUNTS)
    return sum(next(v for n, v in d.items() if n.startswith(c)) * factor for c in codes)


def seed_qb2026(pg) -> None:
    """The new chart's account map and driver structure, exactly as migration 283 seeds them (the test database cleaner empties those tables)."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[3] / "migrations" / "283_proj_pnl_qb2026.sql").read_text()
    for stmt in sql.split(";\n"):
        st = "\n".join(ln for ln in stmt.splitlines() if not ln.lstrip().startswith("--")).strip()
        if st.startswith("INSERT INTO proj_pnl_qb_map") or st.startswith("INSERT INTO proj_pnl_drivers"):
            pg.execute(st)
