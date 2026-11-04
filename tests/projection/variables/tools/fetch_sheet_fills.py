"""Read-only: fetch the background colors of the model tabs from the live Lonvera Projections sheet (service account, spreadsheets.get with a narrow field mask),
keep the non-white fills, and write fills_all.json next to this file. `yellow_inventory.py` turns it into fixtures/yellow_cells.json.

    /Users/joshhoughton/lonvera-sync-main/.venv/bin/python tests/projection/variables/tools/fetch_sheet_fills.py
"""
import sys, json, time
sys.path.insert(0, "/Users/joshhoughton/Desktop/Google Sheets")
import sheets
SID = "1qA0iRJkWp1fbY5b6N860eM88Vs0JRe_8VfPQciMaLKI"
svc = sheets._svc()
TABS = {
 "P&L": "A1:BW215", "Payback": "A1:U175", "Term Debt Monthly Paydown": "A1:AA60", "Curves": "A1:H70",
 "Fixed Expenses": "A1:Z60", "Vendor COGS": "A1:H64", "COGS": "A1:Z80", "COGS Adjustments": "A1:Z40", "Spend-ROAS Matrix": "A1:P41",
 "Automated Pacing ": "A1:AG60", "Month Snapshot": "A1:Z42", "Purchase Orders": "A1:T120", "FBA Transfers": "A1:H55",
 "Warehance Fcst Inputs": "A1:F55", "Amazon Fcst Inputs": "A1:F45", "Intl Fcst Inputs": "A1:F55", "Warehance Reorder": "A1:O10", "Intl Reorder": "A1:O10",
 "Cohort Data - Unified": "A1:EZ45", "Inventory Cash Outlay": "A1:BL25", "Inventory Value": "A1:AN76",
 "Warehance Inventory Burn": "A1:AN52", "Intl Inventory Burn": "A1:AN48", "Amazon Inventory Burn": "A1:AN58", "Total Inventory Burn": "A1:AN65",
 "Warehance Demand Fcst": "A1:AN36", "Amazon Demand Fcst": "A1:AN36", "Intl Demand Fcst": "A1:AN36", "Total Demand Fcst": "A1:AN42",
 "Rollup - Monthly (MoM)": "A1:FW34", "Rollup - Weekly (WoW)": "A1:FD107", "Hist Waterfall - Last 4 Months Curve": "A1:CM60",
}
def qt(t): return "'" + t.replace("'", "''") + "'"
out = {}
for tab, rng in TABS.items():
    for attempt in range(4):
        try:
            r = svc.spreadsheets().get(spreadsheetId=SID, ranges=[f"{qt(tab)}!{rng}"], includeGridData=True,
                fields="sheets(properties(title),data(startRow,startColumn,rowData(values(userEnteredFormat(backgroundColor)))))").execute()
            break
        except Exception as e:
            print("retry", tab, e, file=sys.stderr); time.sleep(20)
    else:
        continue
    cells = {}
    for sh in r["sheets"]:
        for d in sh["data"]:
            r0, c0 = d.get("startRow", 0), d.get("startColumn", 0)
            for i, row in enumerate(d.get("rowData", [])):
                for j, v in enumerate(row.get("values", [])):
                    bg = v.get("userEnteredFormat", {}).get("backgroundColor")
                    if not bg: continue
                    rgb = (round(bg.get("red", 0), 3), round(bg.get("green", 0), 3), round(bg.get("blue", 0), 3))
                    if rgb == (1.0, 1.0, 1.0) or rgb == (0.0, 0.0, 0.0) and False: continue
                    cells[f"{r0+i+1},{c0+j+1}"] = rgb
    out[tab] = cells
    print(tab, len(cells), flush=True)
    time.sleep(1.2)
import pathlib
json.dump(out, open(pathlib.Path(__file__).with_name("fills_all.json"), "w"))
