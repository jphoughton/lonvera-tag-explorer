"""Variables of module `modeler` (page Scenario modeler): the scenario setting that defines EBITDA on the page.
The other knobs on that page are variables of the modules that own them (drivers.spend_total, nc_cpa, nc_aov; cohorts.curve_source, asof_month, n_cohorts), listed there with `also_used_by`."""
from __future__ import annotations

from .build import Builder


def specs():
    from ..modeler import model as mm
    b = Builder("modeler", "modeler")
    b.lever("ebitda_addbacks", "P&L lines added back to net operating income to reach EBITDA", group="EBITDA", unit="P&L lines", vt="list", default=list(mm.DEFAULT_ADDBACKS),
            path=("modeler", "ebitda_addbacks"), choices=tuple(mm.ADDBACK_CHOICES), sens=False, probe="view", extra={"alt": ["interest_expense", "uncategorized"]},
            sheet="No sheet cell: the P&L has no EBITDA line (Pacing's EBITDA adds back marketing influencers and uncategorized expense; interest expense is the default here)",
            used="Yearly EBITDA on the Scenario modeler page = net operating income plus these lines; changes the page's EBITDA only (no engine output).",
            notes="The proof test moves the setting and checks the yearly EBITDA table (tests/projection/variables/test_variables_access.py).")
    return b.specs
