# Tickets for `financing` (from the variables audit)

### tax-payment-months: three payment months, one inconsistent

- **Variable:** `financing.tax_payments` (Sep-27, Jun-28, Sep-29). The module already lists this as an open item (`MODULE_financing.md`, difference 4). A table cell: no code change, Josh's call on Jun-28 versus Sep-28.


## Resolved by proj-financing (payback single store), for proj-variables and proj-pacing to follow

- `payback-live-inputs` and `payback-duplicate` are done in `financing`: the one store is `params['drivers']['payback']` {`product_cost_pct`, `fulfillment_per_order`, `horizon_months`}. `financing.payback.product_cost_pct|fulfillment|max_age` are a read-only legacy fallback (migration 161 removes them from every scenario; a changed value moves to the drivers key first). `PUT /financing/inputs` writes the drivers keys, so the Cash and debt page and the Spend plan page edit one value. `financing.payback.aov|cac` are seeded blank (blank = the drivers' last closed month; typed = override); `GET /financing/inputs` returns `payback_sources`.
- **For the Variables spec** (`specs_financing.py`, not edited here): bind `payback.product_cost_pct`, `payback.fulfillment` and `payback.max_age` to `drivers.payback.product_cost_pct|fulfillment_per_order|horizon_months`, drop the `mirrors`, and mark `payback.aov|cac` "computed unless typed". Test to add there: set the variable on the Variables page, then read it on the Spend plan page and on `GET /financing/payback`.
- **For pacing** (`pacing/levers.py`): the three payback levers should write `drivers.payback.*` (they read `financing.payback.*` first and fall back to drivers, which now works because the financing keys are gone). A write to the financing key while the drivers key is set is ignored by this module.
- **For pacing labels (proj-pacing):** the box uses month-to-date inputs and the plan goal; label them so ("at month to date: AOV, CPA" / "at the month goal") next to Cash and debt's "at last 28 days realized" and "at plan inputs" rows, so two different numbers never read as one payback. `GET /financing/payback?aov=&cac=` with the same inputs still equals the box (pacing's fixwave test passes).
