"""Helpers shared by the inventory_demand tests."""
from __future__ import annotations

from datetime import date, timedelta

P = "/api/projection/inventory-demand"
EPOCH = date(1899, 12, 30)


def serial(d: date) -> int:
    return (d - EPOCH).days


def days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def daily_tab(location: str, rows: dict[date, tuple], caption: str = "") -> list[list]:
    """Sheet values for a daily tab: caption row, blank row, header, then dated rows. `rows[date] = (bull, liver, boneflex, 4th)`."""
    fourth = "Vital Flow" if location == "intl" else "Vital Belle"
    values = [[caption], [], ["Date", "Vital Bull", "Beef Liver", "Boneflex", fourth, "Total"]]
    for d in sorted(rows):
        v = rows[d]
        values.append([serial(d), *v, sum(v)])
    return values


def product_daily_tab(rows: dict[date, tuple], caption: str = "Today's row is partial (same-day capture, not a complete day).") -> list[list]:
    """Sheet values for the long-format Amazon tab: caption row, header row (names carry a description line, as in the sheet), then one row
    per day and product. `rows[date] = (bull, liver, boneflex, belle)`; a 0 is written as a row, None leaves the product out that day."""
    values = [["Date\nDate", "Product\nClassified from sales.sku", "Revenue\nExpandFi sales.total_amount_usd", "Orders\nExpandFi sales.amazon_order_id",
               "Units\nExpandFi sales.quantity_shipped"], [caption]]
    names = ["Vital Bull", "Beef Liver", "Boneflex", "Vital Belle"]
    for d in sorted(rows):
        for name, v in zip(names, rows[d]):
            if v is not None:
                values.append([serial(d), name, v * 30.0, v, v])
    return values


def stock_tab(snap: date, on_hand=((100, 10), (200, 20), (300, 30), (40, 4))) -> list[list]:
    names = ["Vital Bull", "Beef Liver", "Boneflex", "Vital Belle"]
    values = [["Assumptions"], [], [], ["Current inventory"], ["Product", "On hand", "Allocated", "Available"]]
    for n, (oh, al) in zip(names, on_hand):
        values.append([n, oh, al, oh - al])
    values.append(["Snapshot date", serial(snap)])
    return values


class FakeReader:
    """A sheet reader serving fixed tab values; records which tabs were read."""

    def __init__(self, tabs: dict[str, list[list]]):
        self.tabs = tabs
        self.reads: list[str] = []

    def __call__(self, tab: str) -> list[list]:
        self.reads.append(tab)
        if tab not in self.tabs:
            raise RuntimeError(f"no such tab {tab}")
        return self.tabs[tab]
