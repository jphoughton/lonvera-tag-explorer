-- Financing module ("Cash & debt"): seed the Current plan from the sheet's typed cells. Additive and idempotent.
-- Levers live in proj_scenarios.params['financing']; monthly cells in proj_series under the 'financing.' prefix.
-- No tables: everything a scenario can change is a lever or a series (CONTRACT: data conventions), and nothing here is scenario-independent.

UPDATE proj_scenarios
   SET params = jsonb_set(params, '{financing}', $json${
 "cash": {
  "floor": 0.0,
  "opening": 0.0
 },
 "close_month": "2026-09-01",
 "debt": {
  "fixed_payment": 125000.0,
  "mode": "typed",
  "principal": 3000000.0,
  "rate": 0.15
 },
 "matrix": {
  "basis": "cm",
  "fixed_cost": 110000.0,
  "roas": [
   0.4,
   0.45,
   0.5,
   0.55,
   0.6,
   0.65,
   0.7,
   0.75,
   0.8,
   0.85,
   0.9,
   0.95,
   1.0
  ],
  "spends": [
   200000.0,
   300000.0,
   400000.0,
   500000.0,
   600000.0,
   700000.0,
   800000.0,
   900000.0,
   1000000.0,
   1100000.0,
   1200000.0,
   1300000.0,
   1400000.0,
   1500000.0,
   1600000.0,
   1700000.0,
   1800000.0,
   1900000.0,
   2000000.0
  ],
  "target_month": 3
 },
 "payback": {
  "aov": 69.15600858369098,
  "cac": 105.4916738197425,
  "curve": "live",
  "fulfillment": 5.53,
  "max_age": 36,
  "product_cost_pct": 0.1726
 },
 "payout": {
  "aov": 73.0,
  "breakpoint": 1000000.0,
  "cogs_rate": 0.14,
  "columns": [
   {
    "first_bonus": 2000.0,
    "first_scale": 850000.0,
    "payback": 4,
    "step_above": 0.1,
    "step_up": 0.15
   },
   {
    "first_bonus": 3000.0,
    "first_scale": 800000.0,
    "payback": 3,
    "step_above": 0.12,
    "step_up": 0.16
   },
   {
    "first_bonus": 3500.0,
    "first_scale": 800000.0,
    "payback": 2,
    "step_above": 0.1,
    "step_up": 0.15
   }
  ],
  "cpa": 90.0,
  "creative_production_rate": 0.09,
  "designer": 3000.0,
  "fulfillment_rate": 0.09,
  "lazar": 5000.0,
  "media_spend": 800000.0,
  "repeat_months": 7,
  "repeat_source": "goal",
  "repeat_typed": [],
  "sample_cost": 10.0,
  "samples_count": 150,
  "scales": [
   800000.0,
   850000.0,
   900000.0,
   950000.0,
   1000000.0,
   1100000.0,
   1200000.0,
   1300000.0,
   1400000.0,
   1500000.0
  ]
 },
 "seller_note": {
  "ebitda_floor": null,
  "enabled": false,
  "first_payment": "2027-09-01",
  "installments": 3,
  "principal": 1500000.0,
  "rate": 0.05
 },
 "tax": {
  "deduct_interest": false,
  "payments": [
   {
    "from": "2026-09-01",
    "label": "Sep-Dec 2026",
    "month": "2027-09-01",
    "to": "2026-12-01"
   },
   {
    "from": "2027-01-01",
    "label": "2027",
    "month": "2028-06-01",
    "to": "2027-12-01"
   },
   {
    "from": "2028-01-01",
    "label": "2028",
    "month": "2029-09-01",
    "to": "2028-12-01"
   }
  ],
  "rate": 0.21
 }
}$json$::jsonb, true)
 WHERE slug = 'current' AND NOT (params ? 'financing');

INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note)
SELECT s.id, v.line_key, v.month::date, v.value::numeric, v.kind, v.note
  FROM proj_scenarios s,
       (VALUES
  ('financing.principal_paid', '2026-10-01', 180000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E11)'),
  ('financing.principal_paid', '2026-11-01', 150000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E12)'),
  ('financing.principal_paid', '2026-12-01', 50000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E13)'),
  ('financing.principal_paid', '2027-01-01', 100000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E14)'),
  ('financing.principal_paid', '2027-02-01', 100000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E15)'),
  ('financing.principal_paid', '2027-03-01', 140000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E16)'),
  ('financing.principal_paid', '2027-04-01', 109000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E17)'),
  ('financing.principal_paid', '2027-06-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E19)'),
  ('financing.principal_paid', '2027-08-01', 150000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E21)'),
  ('financing.principal_paid', '2027-09-01', 20000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E22)'),
  ('financing.principal_paid', '2027-10-01', 150000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E23)'),
  ('financing.principal_paid', '2027-11-01', 130000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E24)'),
  ('financing.principal_paid', '2027-12-01', 50000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E25)'),
  ('financing.principal_paid', '2028-01-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E26)'),
  ('financing.principal_paid', '2028-02-01', 180000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E27)'),
  ('financing.principal_paid', '2028-04-01', 150000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E29)'),
  ('financing.principal_paid', '2028-05-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E30)'),
  ('financing.principal_paid', '2028-08-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E33)'),
  ('financing.principal_paid', '2028-10-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E35)'),
  ('financing.principal_paid', '2028-11-01', 200000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E36)'),
  ('financing.principal_paid', '2028-12-01', 140000.0, 'input', 'Typed principal paid (Term Debt Monthly Paydown E37)'),
  ('financing.noi_before_inventory', '2026-09-01', 150000.0, 'override', 'Typed in the sheet (P&L W108): the buyer owns only the part of Sep-26 after close'),
  ('financing.opening_cash', '2026-10-01', 0.0, 'override', 'Typed 0 in the sheet (P&L X110): Sep-26 ending cash is not carried into Oct-26. Clear this cell to carry it.')
       ) AS v(line_key, month, value, kind, note)
 WHERE s.slug = 'current'
   AND NOT EXISTS (SELECT 1 FROM proj_series x WHERE x.scenario_id = s.id AND x.line_key LIKE 'financing.%')
ON CONFLICT (scenario_id, line_key, month) DO NOTHING;

