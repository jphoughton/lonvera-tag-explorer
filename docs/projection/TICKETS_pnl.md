# Tickets for `pnl` (from the variables audit)

### parent-driver-not-editable: the Other business expenses driver cannot be saved

- **Variable:** `pnl.driver.other_business_expenses` (rate on total revenue, trailing window, `P&L!B66`).
- **Where:** `app/projection/pnl/api.py` `drivers_put` accepts `L.DRIVER_KEYS + L.DRIVEN_PARENTS` (line 194), but `app/projection/pnl/model.py` `validate_segments` rejects any key outside `L.DRIVER_KEYS` (line 112), and `other_business_expenses` is in `DRIVEN_PARENTS`. The default driver exists and is read, so the line projects; any edit returns 400 "is not a driven line".
- **Fix:** `validate_segments` accepts `DRIVER_KEYS + DRIVEN_PARENTS`.
- **Test:** `PUT /pnl/drivers {key: "other_business_expenses", segments: [{from: null, mode: "rate", base: "total_revenue", rate_kind: "typed", rate: 0.002}]}` returns 200 and `pnl.lines.other_business_expenses` for a projected month equals `0.002 x total_revenue`.

### vendor-retainers-unwired: Fixed Expenses is reference only

- **Variable:** `pnl.vendor_retainers` (10 vendors, current $50,270 and proposed $58,770 a month, `Fixed Expenses!B2:D11`).
- **Where:** `proj_pnl_vendors`; no driver reads it. The sheet does not wire it either.
- **Question for Josh:** wire the proposed retainers into the marketing agency, marketing services and consulting drivers (a toggle between current and proposed), or leave it as reference.
- **Test (if wired):** switching current to proposed raises `marketing_agency` + `marketing_services` + `consulting` by the table's difference.
