# Design tickets: Cohorts and curves (cohorts.js)

Page JS owner: proj-cohorts. Details and screenshots: `DESIGN_REVIEW.md`.

1. **"Save Live as Goal" overwrites the Goal curve in one click (high).** No confirmation, no Undo, and only a toast ("Live curve saved as the Goal curve") that disappears. The Goal curve is the baseline every other number is compared with. Change: `confirmBox` that states what is replaced ("The Goal curve saved on <date> (1.88x over 60 months) is replaced by the Live curve (1.58x). The old Goal stays in History.") with the two lifetime multiples side by side, and an Undo action on the success toast (`ctx.toast(msg, {action})`).
2. **Vintage colours were illegible (fixed by the vet).** The stacked vintage bars mixed the slot colour toward the surface, so the oldest vintages were 1.5:1 to 2.5:1 against the card (dark: 2024 to 2027 cohorts nearly invisible, legend swatches too). `drawRevenue()` now uses `charts.ramp(n, 2)` (every step keeps 3:1 or better in light and dark). Keep it when editing the chart; do not mix toward the surface.
3. **Leftovers (low).** `mixToward`, `base` and `surface` in `drawRevenue()` are now unused; delete them.

## Status (proj-cohorts, 2026-11-03)
1. Done: confirmation with the two multiples and the FY27 net operating income change, the replaced Goal kept as a snapshot (migration 112), "Restore previous Goal" (endpoint, button, audited, versioned) and an Undo action on the success toast. The confirmation is in the Cohorts page next to the Current against Goal table.
2. Kept: `charts.ramp(rows.length, 2)` is unchanged in `drawRevenue()`.
3. Done: `mixToward`, `base` and `surface` deleted.
