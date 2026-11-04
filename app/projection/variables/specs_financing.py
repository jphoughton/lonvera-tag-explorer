"""Variables of module `financing` (page Cash and debt): term loan, seller note, tax, opening cash, payback inputs, spend x ROAS grid and the media-buyer payout block."""
from __future__ import annotations

from .build import Builder

NS = "financing"


def _lever(b, name, label, group, unit, vt, default, path, sheet, used, lo=None, hi=None, step=None, choices=None, tie=None, **kw):
    return b.lever(name, label, group=group, unit=unit, vt=vt, default=default, path=(NS, *path), lo=lo, hi=hi, step=step, choices=choices, sheet=sheet, used=used,
                   sheet_cells=(tie,) if tie else (), **kw)


def specs():
    b = Builder("financing", "financing")
    G = "Term loan"
    _lever(b, "close_month", "Deal close month", G, "month", "month", "2026-09-01", ("close_month",), "Term Debt Monthly Paydown!B10 (=DATE(2026,9,1))",
           "First month of buyer-owned cash and of loan interest; earlier months belong to the seller.", sens=False)
    _lever(b, "debt.principal", "Term loan principal", G, "dollars", "money", 3000000.0, ("debt", "principal"), "Term Debt Monthly Paydown!B4", "Starting balance of the amortization.",
           lo=0, hi=1e10, step=100000, tie=("Term Debt Monthly Paydown", "B4"))
    _lever(b, "debt.rate", "Term loan annual rate", G, "fraction a year", "fraction", 0.15, ("debt", "rate"), "Term Debt Monthly Paydown!B5", "Monthly interest = beginning balance x rate / 12.",
           lo=0, hi=1, step=0.005, tie=("Term Debt Monthly Paydown", "B5"))
    _lever(b, "debt.mode", "Principal payments", G, "typed or fixed_payment", "choice", "typed", ("debt", "mode"), "Term Debt Monthly Paydown!E10:E55 (typed) or B6 (fixed payment)",
           "Typed monthly principal, or a fixed total payment.", choices=("typed", "fixed_payment"), sens=False)
    _lever(b, "debt.fixed_payment", "Fixed monthly payment (fixed-payment mode)", G, "dollars per month", "money", 125000.0, ("debt", "fixed_payment"), "Term Debt Monthly Paydown!B6",
           "Total monthly payment when the mode is fixed payment.", lo=0, hi=1e9, step=5000, tie=("Term Debt Monthly Paydown", "B6"))
    b.series("principal_paid", "Term loan principal paid", group=G, unit="dollars per month", key="financing.principal_paid", cells=21, sheet="Term Debt Monthly Paydown!E10:E55 (21 typed payments)",
             used="Principal paid each month in typed mode; a month without a cell pays interest only.")
    G = "Seller note"
    _lever(b, "seller_note.enabled", "Model the seller note", G, "on or off", "bool", False, ("seller_note", "enabled"), "P&L rows 117-118 (blank) and the LOI text in P&L!A160", "Adds seller-note interest and principal to the cash waterfall.", sens=False)
    _lever(b, "seller_note.principal", "Seller note principal", G, "dollars", "money", 1500000.0, ("seller_note", "principal"), "LOI: $1.5M seller note (P&L!A160 note)", "Amount repaid in installments.", lo=0, hi=1e10, step=100000)
    _lever(b, "seller_note.rate", "Seller note annual rate", G, "fraction a year", "fraction", 0.05, ("seller_note", "rate"), "LOI: 5% (P&L!A160 note)", "Interest on the unpaid balance.", lo=0, hi=1, step=0.005)
    _lever(b, "seller_note.first_payment", "Seller note first installment month", G, "month", "month", "2027-09-01", ("seller_note", "first_payment"), "LOI: annual installments from the first anniversary", "First installment.", sens=False)
    _lever(b, "seller_note.installments", "Seller note installments", G, "count", "integer", 3, ("seller_note", "installments"), "LOI: 36 months, annual installments", "Number of annual installments.", lo=1, hi=20, step=1)
    _lever(b, "seller_note.ebitda_floor", "Seller note earnings floor", G, "dollars", "money", None, ("seller_note", "ebitda_floor"), "LOI: gated on a mutually agreed EBITDA floor (amount not in the sheet)",
           "Installments are skipped in a year below this level.", lo=0, hi=1e10, step=100000, sens=False)
    G = "Tax and cash"
    _lever(b, "tax.rate", "Federal tax rate on net operating income", G, "fraction", "fraction", 0.21, ("tax", "rate"), "Formula literal 0.21 in P&L!AI120, AR120, AU120 (=NOI x 0.21)",
           "Tax paid in each payment month = rate x net operating income of the period it covers.", lo=0, hi=1, step=0.01)
    _lever(b, "tax.deduct_interest", "Deduct loan interest before tax", G, "on or off", "bool", False, ("tax", "deduct_interest"), "No sheet cell (the sheet taxes net operating income)", "Subtracts term-loan interest from the tax base.", sens=False)
    b.table("tax_payments", "Tax payments (payment month and the period each one covers)", group=G, sheet="P&L!AI120 (Sep-27, Sep-Dec 2026), AR120 (Jun-28, 2027), AU120 (Sep-29, 2028)",
            used="When tax cash leaves and which months of net operating income it taxes.", unit="rows", scope="scenario")
    _lever(b, "cash.opening", "Opening cash at the close month", G, "dollars", "money", 0.0, ("cash", "opening"), "P&L!X110 (typed 0, Oct-26 starting cash)", "Starting cash of the waterfall.", lo=-1e10, hi=1e10, step=10000)
    _lever(b, "cash.floor", "Cash floor", G, "dollars", "money", 0.0, ("cash", "floor"), "No sheet cell (added)", "Headroom = ending cash minus this floor.", lo=-1e10, hi=1e10, step=10000, sens=False)
    b.series("noi_before_inventory", "Net operating income before inventory, typed", group=G, unit="dollars per month", key="financing.noi_before_inventory", cells=1, sheet="P&L!W108 (typed 150,000, Sep-26)",
             used="Replaces net operating income plus China COGS for that month.")
    b.series("opening_cash", "Starting cash, typed", group=G, unit="dollars per month", key="financing.opening_cash", cells=1, sheet="P&L!X110 (typed 0, Oct-26)", used="Replaces the carried-forward starting cash for that month.")
    b.series("tax_override", "Tax paid, typed", group=G, unit="dollars per month", key="financing.tax", cells=0, sheet="P&L row 120 (35 typed zeros) and the three formulas", used="Replaces the computed tax for that month.")
    b.series("equity_payback", "Equity paid back", group=G, unit="dollars per month", key="financing.equity_payback", cells=37, sheet="P&L!W122:BJ122 (typed zeros)", used="Equity returned in the month; subtracted from cash.")
    G = "Payback inputs"
    _lever(b, "payback.aov", "Average order value for payback (blank follows pacing)", G, "dollars", "money", None, ("payback", "aov"), "Payback!B7 (=Automated Pacing!M6, live)",
           "First-order revenue in the payback months; blank takes the last closed month from the pacing feed.", lo=0.01, hi=1e6, step=1, sens=False, extra={"computed": "payback_aov"})
    _lever(b, "payback.cac", "Cost per new customer for payback (blank follows pacing)", G, "dollars", "money", None, ("payback", "cac"), "Payback!B8 (=Automated Pacing!M7, live)",
           "Cost recovered by contribution; blank takes the last closed month from the pacing feed.", lo=0, hi=1e6, step=1, sens=False, extra={"computed": "payback_cac"})
    _lever(b, "payback.loaded_lines", "P&L lines counted as a cost of each new-customer order (all-variable-costs payback)", G, "P&L line keys", "list", ["china_cogs", "fulfillment_services", "inbound_shipping", "payment_processing", "chargeback_management"],
           ("payback", "loaded_lines"), "No sheet cell (the sheet's payback uses the typed product cost and fulfillment only)", "Each line's share of DTC revenue over the loaded window is subtracted from an order's value in the all-variable-costs payback.",
           sens=False, extra={"alt": ["china_cogs", "fulfillment_services"]})
    _lever(b, "payback.loaded_months", "Closed months averaged into the variable-cost shares", G, "months", "integer", 12, ("payback", "loaded_months"), "No sheet cell (added)",
           "Window of closed P&L months behind the all-variable-costs shares.", lo=1, hi=36, step=1, sens=False, extra={"alt": 6})
    _lever(b, "payback.loaded_rate", "All-variable-costs share typed over the computed one", G, "fraction", "fraction", None, ("payback", "loaded_rate"), "No sheet cell (added)",
           "When set, replaces the computed variable-cost share of an order in the all-variable-costs payback; blank uses the P&L lines.", lo=0, hi=0.99, step=0.01, sens=False, extra={"alt": 0.45})
    _lever(b, "payback.curve", "Repeat curve for payback", G, "live or goal", "choice", "live", ("payback", "curve"), "Payback column O reads Curves column C (live)", "Which retention curve the payback months use.",
           choices=("live", "goal"), sens=False)
    G = "Spend x ROAS grid"
    _lever(b, "matrix.target_month", "Target payback month", G, "months", "integer", 3, ("matrix", "target_month"), "Spend-ROAS Matrix!B4", "Profit is measured at this month of age.", lo=0, hi=60, step=1, tie=("Spend-ROAS Matrix", "B4"), sens=False)
    _lever(b, "matrix.basis", "Profit basis", G, "cm or loaded", "choice", "cm", ("matrix", "basis"), "Spend-ROAS Matrix!B3 (CM)", "Contribution margin only, or fully loaded with fixed cost.", choices=("cm", "loaded"), sens=False)
    _lever(b, "matrix.margin", "Margin rate behind the grid", G, "product or loaded", "choice", "product", ("matrix", "margin"), "No sheet cell (added)", "Product margin (order value less product cost and fulfillment), or the all-variable-costs margin.",
           choices=("product", "loaded"), sens=False)
    _lever(b, "matrix.fixed_cost", "Fixed cost per month", G, "dollars per month", "money", 110000.0, ("matrix", "fixed_cost"), "Spend-ROAS Matrix!B7", "Subtracted on the fully loaded basis.", lo=0, hi=1e10, step=5000,
           tie=("Spend-ROAS Matrix", "B7"), sens=False)
    _lever(b, "matrix.spends", "Monthly spend levels in the grid", G, "dollars (list)", "list", [200000.0 + 100000.0 * i for i in range(19)], ("matrix", "spends"), "Spend-ROAS Matrix!A12:A30", "Rows of the profit grid.", sens=False, extra={"alt": [100000.0, 250000.0]})
    _lever(b, "matrix.roas", "New-customer ROAS levels in the grid", G, "multiples (list)", "list", [round(0.4 + 0.05 * i, 2) for i in range(13)], ("matrix", "roas"), "Spend-ROAS Matrix!B11:N11", "Columns of the profit grid.", sens=False, extra={"alt": [0.5, 0.6]})
    G = "Media-buyer payout block"
    PO = ("payout",)
    _lever(b, "payout.media_spend", "Monthly media spend", G, "dollars per month", "money", 800000.0, PO + ("media_spend",), "P&L!F180", "Scale used for payback and the bonus tier.", lo=0, hi=1e10, step=50000, tie=("P&L", "F180"), sens=False)
    _lever(b, "payout.cpa", "Cost per new customer", G, "dollars", "money", 90.0, PO + ("cpa",), "P&L!F182", "New customers = media spend / this.", lo=0.01, hi=1e6, step=1, tie=("P&L", "F182"), sens=False)
    _lever(b, "payout.aov", "New-customer average order value", G, "dollars", "money", 73.0, PO + ("aov",), "P&L!F183", "New-customer revenue = customers x this.", lo=0.01, hi=1e6, step=1, tie=("P&L", "F183"), sens=False)
    _lever(b, "payout.lazar", "Retainer (Lazar)", G, "dollars per month", "money", 5000.0, PO + ("lazar",), "P&L!G205", "Marketing expense and the retainer added to total pay.", lo=0, hi=1e9, step=500, tie=("P&L", "G205"), sens=False)
    _lever(b, "payout.designer", "Designer", G, "dollars per month", "money", 3000.0, PO + ("designer",), "P&L!G206", "Marketing expense.", lo=0, hi=1e9, step=500, tie=("P&L", "G206"), sens=False)
    _lever(b, "payout.samples_count", "Samples sent per month", G, "count", "integer", 150, PO + ("samples_count",), "P&L!J205", "Samples expense = count x cost.", lo=0, hi=100000, step=10, tie=("P&L", "J205"), sens=False)
    _lever(b, "payout.sample_cost", "Cost per sample", G, "dollars", "money", 10.0, PO + ("sample_cost",), "P&L!J206", "Samples expense = count x cost.", lo=0, hi=1e6, step=1, tie=("P&L", "J206"), sens=False)
    _lever(b, "payout.creative_production_rate", "Creative production, share of media spend", G, "fraction", "fraction", 0.09, PO + ("creative_production_rate",), "P&L!I208 (typed 0.09)",
           "Creative production cost (an expense, not pay).", lo=0, hi=1, step=0.01, tie=("P&L", "I208"), sens=False)
    _lever(b, "payout.cogs_rate", "Cost of goods, share of new-customer revenue", G, "fraction", "fraction", 0.14, PO + ("cogs_rate",), "P&L!G186", "Contribution margin of the block.", lo=0, hi=1, step=0.01, tie=("P&L", "G186"), sens=False)
    _lever(b, "payout.fulfillment_rate", "Fulfillment, share of new-customer revenue", G, "fraction", "fraction", 0.09, PO + ("fulfillment_rate",), "P&L!G187", "Contribution margin of the block.", lo=0, hi=1, step=0.01, tie=("P&L", "G187"), sens=False)
    _lever(b, "payout.repeat_source", "Repeat curve for the payout block", G, "goal, live or typed", "choice", "goal", PO + ("repeat_source",), "P&L!J180:J186 (typed M1 to M7 repeat ratios equal the goal curve)",
           "Which repeat ratios drive running contribution.", choices=("goal", "live", "typed"), sens=False)
    _lever(b, "payout.repeat_months", "Months of repeat counted", G, "months", "integer", 7, PO + ("repeat_months",), "P&L!I180:I186 (7 months)", "Length of the running contribution table.", lo=1, hi=36, step=1, sens=False)
    _lever(b, "payout.repeat_typed", "Typed repeat ratios (when the source is typed)", G, "ratios (list)", "list", [], PO + ("repeat_typed",), "P&L!J180:J186", "Repeat ratios by month of age.", sens=False, extra={"alt": [0.2, 0.15, 0.1], "setup": {"financing.payout.repeat_source": "typed"}})
    _lever(b, "payout.scales", "Bonus scale tiers", G, "dollars (list)", "list", [800000.0, 850000.0, 900000.0, 950000.0, 1000000.0, 1100000.0, 1200000.0, 1300000.0, 1400000.0, 1500000.0], PO + ("scales",),
           "P&L!G193:G202", "Rows of the bonus schedule.", sens=False, extra={"alt": [700000.0, 800000.0, 900000.0]})
    _lever(b, "payout.breakpoint", "Scale where the step-up drops", G, "dollars", "money", 1000000.0, PO + ("breakpoint",), "P&L!H197:H198 (15% bumps up to $1.0M, 10% above)", "Tier at which the second step-up applies.", lo=0, hi=1e10, step=50000, sens=False)
    b.table("payout_columns", "Payback columns of the bonus schedule (payback month, first tier, first bonus, step-ups)", group=G,
            sheet="P&L!H193:I194 and the 1.15 / 1.16 / 1.1 / 1.12 literals in H194:I202", used="Bonus by scale tier and payback month; total pay adds the retainer.", unit="rows")
    G = "Sheet items the app does not carry"
    b.missing("interest_on_cash", "Interest earned on cash balances", group=G, unit="fraction a year", default=0.1, sheet="P&L!B131 (typed 0.1) feeding row 132",
              used="Only P&L row 132, which no cash formula reads.", notes="Unused in the sheet; the cash waterfall earns no interest.")
    return b.specs
