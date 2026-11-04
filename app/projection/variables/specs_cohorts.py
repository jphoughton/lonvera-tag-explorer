"""Variables of module `cohorts`: the retention curve and the repeat-revenue waterfall (sheet tabs Cohort Data - Unified, Curves, Hist Waterfall)."""
from __future__ import annotations

from .build import Builder


def _curve_validator(v):
    from ..cohorts import engine as eng
    return eng.parse_curve_choice(v)


def specs():
    from ..cohorts import engine as eng
    b = Builder("cohorts", "cohorts")
    G = "Retention curve"
    b.lever("n_cohorts", "Cohorts averaged into the live curve", group=G, unit="cohorts", vt="integer", default=3, path=("cohorts", "n_cohorts"), lo=1, hi=36, step=1,
            sheet="Cohort Data - Unified row 36, literal 3 in 'Last 3 Cohorts Avg' (H36:EZ36)", used="Live curve at each month of age = weighted average of the increment ratio of the most recent N cohorts that reached that age.",
            notes="The sheet reads the last 3 cohorts; check whether the last 6 is the intended plan of record.", also_used_by=("modeler",))
    b.lever("min_cohorts_per_age", "Fewest cohorts that make a month of age trustworthy", group=G, unit="cohorts", vt="integer", default=1,
            path=("cohorts", "min_cohorts_per_age"), lo=1, hi=36, step=1, sheet="Cohort Data - Unified row 36, MIN(3, count) in H36:EZ36",
            used="An age with fewer cohorts than this reads the previous age times the tail decay instead of the table.")
    b.lever("tail_from_age", "First month of age the curve decays instead of reading the table", group=G, unit="months of age", vt="integer", default=28,
            path=("cohorts", "tail_from_age"), lo=2, hi=60, step=1, sheet="Curves rows 4-30 read the cohort table (ages 1-27); C31 starts the decay",
            used="Ages from this month on equal the previous age times the tail decay.")
    b.lever("tail_decay", "Tail decay per month", group=G, unit="fraction kept each month", vt="fraction", default=0.95, path=("cohorts", "tail_decay"), lo=0.5, hi=1.0, step=0.01,
            sheet="Curves!C2", used="Share of the previous month's repeat dollars kept in each month of the tail; moves lifetime repeat revenue of every cohort.",
            sheet_cells=(("Curves", "C2"),))
    b.lever("max_age", "Last month of age that earns repeat revenue", group=G, unit="months of age", vt="integer", default=60, path=("cohorts", "max_age"), lo=12, hi=60, step=1,
            sheet="Hist Waterfall - Last 4 Months Curve!C82 (literal 60 in IF(age>60,0,...))", used="No repeat revenue beyond this month of age.")
    b.lever("curve_source", "Curve that drives repeat revenue", group=G, unit="live, goal, asof or blend:<weight on goal>", vt="text", default="live", path=("cohorts", "curve_source"),
            sheet="P&L!A2 'Repeat Case' (Last 4 Months Curve)", used="Chooses the current curve, the saved goal curve, the curve as of an earlier month, or a blend of current and goal for every cohort's repeat revenue.",
            validator=_curve_validator, probe="engine", sens=False, extra={"alt": "goal"},
            notes="Replaces the earlier lever name 'curve' (still read). Picked on the P&L page's Retention curve card and on the Cohorts page.", also_used_by=("modeler",))
    b.lever("asof_month", "Month the as-of curve is taken at the end of", group=G, unit="first of a month", vt="month", default=None, path=("cohorts", "asof_month"),
            sheet="No sheet cell: the sheet holds only the current curve and one Goal snapshot (Curves!B4:B63, 2026-09-29)",
            used="With curve source as-of, the current-curve rule is applied to the cohort table as it stood at the end of this month (cells landing later are ignored); the first month with 3 cohorts at each of the first 12 months of age is Oct-25 in the data today.",
            probe="engine", sens=False, extra={"setup": {"cohorts.curve_source": "asof"}, "alt": "2026-08-01"}, also_used_by=("modeler",))
    G3 = "Upload checks"
    note = "Only the cohort upload preview reads it: a file that breaks the check cannot be loaded."
    for name, label, unit, vt, step, used in (
            ("upload_lifetime_min", "Lowest allowed repeat per first-order dollar over 60 months", "multiple", "number", 0.1, "An upload that would push the 60-month multiple below this cannot be loaded."),
            ("upload_lifetime_max", "Highest allowed repeat per first-order dollar over 60 months", "multiple", "number", 0.5, "An upload that would push the 60-month multiple above this cannot be loaded."),
            ("upload_twelve_min", "Lowest allowed repeat per first-order dollar over the first 12 months", "multiple", "number", 0.1, "An upload that would push the 12-month multiple below this cannot be loaded."),
            ("upload_twelve_max", "Highest allowed repeat per first-order dollar over the first 12 months", "multiple", "number", 0.5, "An upload that would push the 12-month multiple above this cannot be loaded."),
            ("upload_first_order_min", "Lowest allowed first order per customer", "dollars", "money", 1, "A cohort whose first order per customer is below this cannot be loaded."),
            ("upload_first_order_max", "Highest allowed first order per customer", "dollars", "money", 10, "A cohort whose first order per customer is above this cannot be loaded."),
            ("upload_first_order_tol", "How far a first order may sit from the cohorts around it", "fraction", "fraction", 0.05, "A new or changed cohort of 1,000 customers or more whose first order is further than this from its neighbours' typical value cannot be loaded."),
            ("upload_customers_tol", "How far a stored cohort's customer count may change", "fraction", "fraction", 0.05, "A re-upload that changes a stored cohort's customer count by more than this cannot be loaded.")):
        lo, hi = {"upload_lifetime_min": (0, 10), "upload_lifetime_max": (0.5, 50), "upload_twelve_min": (0, 10), "upload_twelve_max": (0.2, 20), "upload_first_order_min": (0, 10000),
                  "upload_first_order_max": (1, 100000), "upload_first_order_tol": (0.05, 5), "upload_customers_tol": (0.05, 10)}[name]
        default = eng.BAND_BOUNDS[name][0]
        b.lever(name, label, group=G3, unit=unit, vt=vt, default=default, path=("cohorts", name), lo=lo, hi=hi, step=step, sheet="No sheet cell (new): the sheet accepted any pasted number",
                used=used, probe="none", sens=False, notes=note)
    b.table("goal_curve", "Goal retention curve (60 months of age)", group=G, sheet="Curves!B4:B63 (snapshot 2026-09-29, typed)", unit="values",
            used="Saved curve the 'goal' and 'blend' choices read; each save is a dated snapshot.", scope="all")
    b.table("cohort_table", "Cohort table (one row per acquisition month, cumulative dollars per customer by month of age)", group="Cohort data",
            sheet="Cohort Data - Unified A2:EZ34 (33 cohorts, Feb-24 to Oct-26)", used="Source of first-order revenue, new customers and every observed increment; uploaded closed months only.", scope="all")
    G2 = "Upload and page rules"
    b.missing("upload.min_customers", "Smallest cohort (new customers) the upload keeps", group=G2, unit="customers", vt="integer", default=10, scope="feed",
              sheet="No sheet cell (the sheet had every cohort row, however small)", used="A cohort smaller than this is skipped on upload so it cannot distort the curve.",
              ticket="cohorts#upload-rules", notes="Hard-coded as upload.MIN_CUSTOMERS; the upload preview accepts ?min_customers= for one run.")
    b.missing("freshness.stale_after_days", "Cohort export counts as stale after", group=G2, unit="days", vt="integer", default=45, scope="feed",
              sheet="No sheet cell", used="Days after the last loaded month's end before the page and the daily check call the export stale.", ticket="cohorts#upload-rules",
              notes="Hard-coded as store.STALE_AFTER_DAYS.")
    b.missing("backtest_months", "Months in the model-against-actual check", group=G2, unit="months", vt="integer", default=3, sheet="No sheet cell",
              used="The page compares modeled and actual repeat revenue over this many closed months.", ticket="cohorts#upload-rules", notes="Hard-coded in views.py (last 3 months).")
    b.missing("hist_waterfall_typed_nc_rev", "New-customer revenue typed beside each cohort on the waterfall tab", group="Cohort data", unit="dollars", vt="money",
              sheet="Hist Waterfall - Last 4 Months Curve!B6:B35 (typed, stale)", used="The sheet applies the curve to these typed figures; the app uses the cohort table (new customers x first-order dollars).",
              notes="Not a variable on purpose: the typed figures differ from the table by -4.2% to +5.5% per cohort because they were not refreshed with the table.", scope="all")
    return b.specs
