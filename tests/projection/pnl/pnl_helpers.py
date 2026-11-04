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
        line("Total Cost of Goods Sold", [300.0, 320.0, 340.0][:n]),
        line("Gross Profit", [1100.0, 2180.0, 3260.0][:n]),
        ["Expenses"],
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
