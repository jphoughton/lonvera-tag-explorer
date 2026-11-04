"""Read sheet tabs either from a frozen snapshot dir (JSON `values` arrays) or LIVE from Google Sheets (read-only).
Live auth: env GOOGLE_SA_JSON (service-account JSON string) else the local key file. Never writes to Sheets."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

CK_SHEET = "12LmoKXLB-fXw9jA5VvfqzVPdgZrVnglowqVbwrKCaNI"          # Lonvera Creative Key
PROJ_SHEET = "1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI"        # Lonvera Projections
LOCAL_KEY = Path.home() / ".gcloud-keys" / "shopify-data-451316-d67b76e78c82.json"

# snapshot file stem -> (spreadsheet id, tab name, valueRenderOption)
TABS = {
    "creative_tracker": (CK_SHEET, "Creative Tracker", "FORMATTED_VALUE"),
    "communication_overview": (CK_SHEET, "Communication Overview", "FORMATTED_VALUE"),
    "lists": (CK_SHEET, "Lists", "FORMATTED_VALUE"),
    "meta_daily": (CK_SHEET, "Meta Ads - Daily", "UNFORMATTED_VALUE"),
    "meta_lifetime": (CK_SHEET, "Meta Ads - Lifetime", "UNFORMATTED_VALUE"),
    "meta_windows": (CK_SHEET, "Meta Ads - Windows", "UNFORMATTED_VALUE"),
    "meta_campaign_history": (PROJ_SHEET, "Meta Campaign - History", "UNFORMATTED_VALUE"),
    "overview_metrics": (CK_SHEET, "VitaBull - Overview Metrics", "UNFORMATTED_VALUE"),
    "rollups": (CK_SHEET, "VitaBull - Roll-ups", "UNFORMATTED_VALUE"),
    "tag_breakout": (CK_SHEET, "VitaBull - Tag Breakout", "UNFORMATTED_VALUE"),
    "tag_map": (CK_SHEET, "VitaBull Tag Map", "UNFORMATTED_VALUE"),
}

_svc = None


def _service():
    global _svc
    if _svc is None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        raw = os.environ.get("GOOGLE_SA_JSON")
        if raw:
            creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
        else:
            key = Path(os.environ.get("GOOGLE_SA_FILE") or LOCAL_KEY)
            if not key.exists():
                raise RuntimeError("No Google credentials: set GOOGLE_SA_JSON or GOOGLE_SA_FILE")
            creds = service_account.Credentials.from_service_account_file(str(key), scopes=scopes)
        _svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _svc


def read_tab(stem: str, snapshot_dir: str | os.PathLike | None = None) -> list[list]:
    """Return the tab's rows as a list of lists. snapshot_dir given -> read <dir>/<stem>.json, else read the live sheet."""
    if snapshot_dir:
        p = Path(snapshot_dir) / f"{stem}.json"
        data = json.loads(p.read_text())
        return data["values"] if isinstance(data, dict) and "values" in data else data
    sid, tab, render = TABS[stem]
    res = _service().spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{tab}'!A1:ZZ", valueRenderOption=render,
        dateTimeRenderOption="SERIAL_NUMBER").execute()
    return res.get("values", [])


# ---------- value helpers ----------
EPOCH = dt.date(1899, 12, 30)


def serial_to_date(v) -> dt.date | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return EPOCH + dt.timedelta(days=int(v))
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}", s):
        return EPOCH + dt.timedelta(days=int(s))
    return parse_date(s)


def parse_date(s) -> dt.date | None:
    """Parse tracker date strings: m/d/yyyy, m/d/yy, yyyy-mm-dd, 5-digit Sheets serial. None if blank/unparseable."""
    s = str(s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}", s):
        return EPOCH + dt.timedelta(days=int(s))
    for f in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def num(x, default: float = 0.0) -> float:
    if isinstance(x, bool):
        return default
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("$", "")
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def cell(row, i, default=""):
    return row[i] if i < len(row) and row[i] is not None else default


def header_index(header: list) -> dict:
    """header text -> column index (stripped); duplicate headers get '#2', '#3' suffixes."""
    idx: dict = {}
    for i, h in enumerate(header):
        k = str(h).strip()
        if k in idx:
            n = 2
            while f"{k}#{n}" in idx:
                n += 1
            k = f"{k}#{n}"
        idx[k] = i
    return idx
