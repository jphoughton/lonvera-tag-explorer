"""Minimal .xlsx reader (standard library only): the cell values of the first worksheet as a list of rows.

QuickBooks exports and Excel save plain worksheets, so this reads shared strings, inline strings, numbers, booleans and dates (as Excel serial
numbers: the caller decides what a number means). Formulas are not evaluated (the cached value is read). Hardened for uploads: the archive's
member count and total uncompressed size are capped, and any XML part that declares a DOCTYPE or ENTITY is refused (no entity expansion).
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

MAX_MEMBERS = 200
MAX_UNCOMPRESSED = 60 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLS = 300
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_DANGEROUS = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)
_REF = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxError(ValueError):
    pass


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    data = zf.read(name)
    if _DANGEROUS.search(data):
        raise XlsxError("the workbook contains an XML construct that is not allowed")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise XlsxError(f"the workbook could not be read ({name})") from e


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(t.text or "" for t in el.iter(f"{{{NS['m']}}}t"))


def read_first_sheet(data: bytes) -> list[list[Any]]:
    """Rows of the first worksheet (workbook order). Empty cells are None; numbers are float; text is str."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise XlsxError("not an .xlsx file") from e
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise XlsxError("the workbook is too large")
    names = {i.filename for i in infos}
    if "xl/workbook.xml" not in names:
        raise XlsxError("not an .xlsx workbook")
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in _xml(zf, "xl/sharedStrings.xml").findall("m:si", NS):
            shared.append(_text(si))
    wb = _xml(zf, "xl/workbook.xml")
    first = wb.find("m:sheets/m:sheet", NS)
    if first is None:
        raise XlsxError("the workbook has no sheets")
    rid = first.get(f"{{{NS['r']}}}id")
    target = None
    if "xl/_rels/workbook.xml.rels" in names:
        for rel in _xml(zf, "xl/_rels/workbook.xml.rels").findall("pr:Relationship", NS):
            if rel.get("Id") == rid:
                target = rel.get("Target") or ""
    path = None
    if target:
        t = target.lstrip("/")
        path = t if t.startswith("xl/") else "xl/" + t
    if not path or path not in names:
        path = next((n for n in sorted(names) if n.startswith("xl/worksheets/") and n.endswith(".xml")), None)
    if not path:
        raise XlsxError("the workbook has no worksheet")
    sheet = _xml(zf, path)
    rows: dict[int, dict[int, Any]] = {}
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        for c in row.findall("m:c", NS):
            m = _REF.match(c.get("r") or "")
            if not m:
                continue
            ci, ri = _col_index(m.group(1)), int(m.group(2)) - 1
            if ri >= MAX_ROWS or ci >= MAX_COLS:
                continue
            t = c.get("t")
            v = c.find("m:v", NS)
            val: Any = None
            if t == "s" and v is not None and v.text is not None:
                idx = int(v.text)
                val = shared[idx] if 0 <= idx < len(shared) else None
            elif t == "inlineStr":
                val = _text(c.find("m:is", NS))
            elif t in ("str", "e") and v is not None:
                val = v.text
            elif t == "b" and v is not None:
                val = bool(int(v.text or 0))
            elif v is not None and v.text not in (None, ""):
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            if val is not None and val != "":
                rows.setdefault(ri, {})[ci] = val
    if not rows:
        return []
    out: list[list[Any]] = []
    for ri in range(max(rows) + 1):
        r = rows.get(ri, {})
        width = (max(r) + 1) if r else 0
        out.append([r.get(ci) for ci in range(width)])
    return out
