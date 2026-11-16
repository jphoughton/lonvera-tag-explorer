"""Countries (Marketing metrics, platform Meta): per country, Meta spend and the new customers and revenue the business actually got, for any date window.

Spend, impressions, link clicks and Meta-reported purchase value come from Meta's own country breakdown (analytics.meta_breakdown_daily, breakdown = country; both ad
accounts; Meta-reported). New customers and their revenue come from Shopify through the ExpandFi warehouse (public.nc_by_country_daily, feed nc_by_country_daily): each
customer's FIRST Shopify order ever, dated on its Pacific day, by ship-to country (billing country when there is none, else unknown), revenue = the order total in dollars.
That is every channel, so Google and organic buyers are in it; Upstack has no country dimension, which is why this table is not "Upstack new-customer" numbers.

Shape (same as app/amazon.py): `build()` is a pure function of a *source* object; `DbSource` reads Postgres and tests/ui/mock_countries.py feeds the SAME builder from made-up rows.
Rules (docs/COUNTRIES.md):
  * every ratio is a ratio of sums, never an average of daily ratios; NULL is not zero (a country or table with no data reads blank);
  * the window is cut to the days BOTH tables hold: it starts no earlier than the first day of the country breakdown (and of the new-customer table when that is loaded) and
    ends no later than the last day of either, so spend and new-customer revenue always cover the same days; the payload says what was cut and why;
  * total NC ROAS = the new-customer revenue of ALL countries (including countries with no spend) / total Meta spend.
"""
from __future__ import annotations

import datetime as dt

from . import queries as q

COUNTRY_NAMES = {
    "AF": "Afghanistan", "AX": "Aland Islands", "AL": "Albania", "DZ": "Algeria", "AS": "American Samoa", "AD": "Andorra", "AO": "Angola", "AI": "Anguilla",
    "AQ": "Antarctica", "AG": "Antigua and Barbuda", "AR": "Argentina", "AM": "Armenia", "AW": "Aruba", "AU": "Australia", "AT": "Austria", "AZ": "Azerbaijan",
    "BS": "Bahamas", "BH": "Bahrain", "BD": "Bangladesh", "BB": "Barbados", "BY": "Belarus", "BE": "Belgium", "BZ": "Belize", "BJ": "Benin", "BM": "Bermuda",
    "BT": "Bhutan", "BO": "Bolivia", "BQ": "Bonaire, Sint Eustatius and Saba", "BA": "Bosnia and Herzegovina", "BW": "Botswana", "BV": "Bouvet Island", "BR": "Brazil",
    "IO": "British Indian Ocean Territory", "BN": "Brunei", "BG": "Bulgaria", "BF": "Burkina Faso", "BI": "Burundi", "CV": "Cabo Verde", "KH": "Cambodia",
    "CM": "Cameroon", "CA": "Canada", "KY": "Cayman Islands", "CF": "Central African Republic", "TD": "Chad", "CL": "Chile", "CN": "China", "CX": "Christmas Island",
    "CC": "Cocos (Keeling) Islands", "CO": "Colombia", "KM": "Comoros", "CG": "Congo", "CD": "Congo (Democratic Republic)", "CK": "Cook Islands", "CR": "Costa Rica",
    "CI": "Cote d'Ivoire", "HR": "Croatia", "CU": "Cuba", "CW": "Curacao", "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "DJ": "Djibouti", "DM": "Dominica",
    "DO": "Dominican Republic", "EC": "Ecuador", "EG": "Egypt", "SV": "El Salvador", "GQ": "Equatorial Guinea", "ER": "Eritrea", "EE": "Estonia", "SZ": "Eswatini",
    "ET": "Ethiopia", "FK": "Falkland Islands", "FO": "Faroe Islands", "FJ": "Fiji", "FI": "Finland", "FR": "France", "GF": "French Guiana", "PF": "French Polynesia",
    "TF": "French Southern Territories", "GA": "Gabon", "GM": "Gambia", "GE": "Georgia", "DE": "Germany", "GH": "Ghana", "GI": "Gibraltar", "GR": "Greece",
    "GL": "Greenland", "GD": "Grenada", "GP": "Guadeloupe", "GU": "Guam", "GT": "Guatemala", "GG": "Guernsey", "GN": "Guinea", "GW": "Guinea-Bissau", "GY": "Guyana",
    "HT": "Haiti", "HM": "Heard Island and McDonald Islands", "VA": "Holy See", "HN": "Honduras", "HK": "Hong Kong", "HU": "Hungary", "IS": "Iceland", "IN": "India",
    "ID": "Indonesia", "IR": "Iran", "IQ": "Iraq", "IE": "Ireland", "IM": "Isle of Man", "IL": "Israel", "IT": "Italy", "JM": "Jamaica", "JP": "Japan", "JE": "Jersey",
    "JO": "Jordan", "KZ": "Kazakhstan", "KE": "Kenya", "KI": "Kiribati", "KP": "North Korea", "KR": "South Korea", "XK": "Kosovo", "KW": "Kuwait", "KG": "Kyrgyzstan",
    "LA": "Laos", "LV": "Latvia", "LB": "Lebanon", "LS": "Lesotho", "LR": "Liberia", "LY": "Libya", "LI": "Liechtenstein", "LT": "Lithuania", "LU": "Luxembourg",
    "MO": "Macao", "MG": "Madagascar", "MW": "Malawi", "MY": "Malaysia", "MV": "Maldives", "ML": "Mali", "MT": "Malta", "MH": "Marshall Islands", "MQ": "Martinique",
    "MR": "Mauritania", "MU": "Mauritius", "YT": "Mayotte", "MX": "Mexico", "FM": "Micronesia", "MD": "Moldova", "MC": "Monaco", "MN": "Mongolia", "ME": "Montenegro",
    "MS": "Montserrat", "MA": "Morocco", "MZ": "Mozambique", "MM": "Myanmar", "NA": "Namibia", "NR": "Nauru", "NP": "Nepal", "NL": "Netherlands",
    "NC": "New Caledonia", "NZ": "New Zealand", "NI": "Nicaragua", "NE": "Niger", "NG": "Nigeria", "NU": "Niue", "NF": "Norfolk Island", "MK": "North Macedonia",
    "MP": "Northern Mariana Islands", "NO": "Norway", "OM": "Oman", "PK": "Pakistan", "PW": "Palau", "PS": "Palestine", "PA": "Panama", "PG": "Papua New Guinea",
    "PY": "Paraguay", "PE": "Peru", "PH": "Philippines", "PN": "Pitcairn", "PL": "Poland", "PT": "Portugal", "PR": "Puerto Rico", "QA": "Qatar", "RE": "Reunion",
    "RO": "Romania", "RU": "Russia", "RW": "Rwanda", "BL": "Saint Barthelemy", "SH": "Saint Helena", "KN": "Saint Kitts and Nevis", "LC": "Saint Lucia",
    "MF": "Saint Martin", "PM": "Saint Pierre and Miquelon", "VC": "Saint Vincent and the Grenadines", "WS": "Samoa", "SM": "San Marino",
    "ST": "Sao Tome and Principe", "SA": "Saudi Arabia", "SN": "Senegal", "RS": "Serbia", "SC": "Seychelles", "SL": "Sierra Leone", "SG": "Singapore",
    "SX": "Sint Maarten", "SK": "Slovakia", "SI": "Slovenia", "SB": "Solomon Islands", "SO": "Somalia", "ZA": "South Africa",
    "GS": "South Georgia and the South Sandwich Islands", "SS": "South Sudan", "ES": "Spain", "LK": "Sri Lanka", "SD": "Sudan", "SR": "Suriname",
    "SJ": "Svalbard and Jan Mayen", "SE": "Sweden", "CH": "Switzerland", "SY": "Syria", "TW": "Taiwan", "TJ": "Tajikistan", "TZ": "Tanzania", "TH": "Thailand",
    "TL": "Timor-Leste", "TG": "Togo", "TK": "Tokelau", "TO": "Tonga", "TT": "Trinidad and Tobago", "TN": "Tunisia", "TR": "Turkey", "TM": "Turkmenistan",
    "TC": "Turks and Caicos Islands", "TV": "Tuvalu", "UG": "Uganda", "UA": "Ukraine", "AE": "United Arab Emirates", "GB": "United Kingdom",
    "UM": "United States Minor Outlying Islands", "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan", "VU": "Vanuatu", "VE": "Venezuela", "VN": "Vietnam",
    "VG": "British Virgin Islands", "VI": "U.S. Virgin Islands", "WF": "Wallis and Futuna", "EH": "Western Sahara", "YE": "Yemen", "ZM": "Zambia", "ZW": "Zimbabwe",
    "AC": "Ascension Island", "TA": "Tristan da Cunha", "EU": "European Union",
}
UNKNOWN = "unknown"
EMPTY_MESSAGE = "Country data has not loaded yet"


def country_name(code: str | None) -> str:
    c = (code or "").strip().upper()
    if not c or c.lower() == UNKNOWN:
        return "Unknown"
    return COUNTRY_NAMES.get(c, c)


def _div(a, b, nd: int) -> float | None:
    if a is None or b is None or float(b) == 0:
        return None
    return round(float(a) / float(b), nd)


def _f(x):
    return None if x is None else float(x)


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _day_label(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


# ---------------------------------------------------------------------------------------------------------------- the source (Postgres)
class DbSource:
    """Reads the two tables. spans() -> {"spend": (first, last) | None, "nc": (first, last) | None}."""

    def __init__(self, conn):
        self.conn = conn

    def spans(self) -> dict:
        r = self.conn.execute("SELECT min(day) AS a, max(day) AS b FROM analytics.meta_breakdown_daily WHERE breakdown = 'country'").fetchone()
        s = (r["a"], r["b"]) if r and r["a"] else None
        r = self.conn.execute("SELECT min(day) AS a, max(day) AS b FROM nc_by_country_daily").fetchone()
        n = (r["a"], r["b"]) if r and r["a"] else None
        return {"spend": s, "nc": n}

    def spend(self, lo: dt.date, hi: dt.date) -> list[dict]:
        rows = self.conn.execute(
            "SELECT upper(value) AS country, sum(spend) AS spend, sum(impressions) AS impressions, sum(link_clicks) AS link_clicks, sum(purchase_value) AS purchase_value "
            "FROM analytics.meta_breakdown_daily WHERE breakdown = 'country' AND day BETWEEN %s AND %s AND value IS NOT NULL GROUP BY 1", (lo, hi)).fetchall()
        return [dict(r) for r in rows]

    def nc(self, lo: dt.date, hi: dt.date) -> list[dict]:
        rows = self.conn.execute(
            "SELECT upper(country_code) AS country, sum(new_customers) AS new_customers, sum(new_customer_revenue) AS revenue "
            "FROM nc_by_country_daily WHERE day BETWEEN %s AND %s GROUP BY 1", (lo, hi)).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- window
def window_info(w: q.WindowSpec, meta: q.DataMeta, spans: dict) -> dict:
    info = w.info(meta)
    first = spans["spend"][0] if spans.get("spend") else meta.daily_start
    info["channel"] = "meta"
    info["source"] = {"key": "meta", "basis": None, "label": "Meta country breakdown and Shopify new customers",
                      "labels": {"purchases": "New customers", "revenue": "New-customer revenue", "roas": "NC ROAS", "cpa": "NC CPA"},
                      "model": None, "channel": "meta", "summary": "Spend is Meta's own country breakdown; new customers are each customer's first Shopify order, by ship-to country.",
                      "note": None, "history_from": _iso(first), "through": _iso(meta.data_through), "built_at": None}
    info["stage"], info["stages"], info["stage_note"] = "all", list(q.STAGES), ""
    if w.is_launch:
        info["label"] = f"Since launch · country data from {q._fmt_day(first, True)}"
    return info


def empty_payload(message: str = EMPTY_MESSAGE) -> dict:
    return {"channel": "meta", "view": "countries", "loaded": False, "message": message, "rows": [], "total": None, "window_info": None}


# ---------------------------------------------------------------------------------------------------------------- the table
def _metrics(spend, imps, clicks, pval, n, rev, total_spend) -> dict:
    """Every column of one row from its sums (a row and the Total row share this)."""
    return {
        "spend": None if spend is None else round(spend, 2), "share": _div(spend, total_spend, 4),
        "new_customers": n, "nc_revenue": None if rev is None else round(rev, 2),
        "nc_roas": _div(rev, spend, 2), "nc_cpa": _div(spend, n, 2), "aov": _div(rev, n, 2),
        "cpm": _div(None if spend is None else spend * 1000, imps, 2), "ctr": _div(clicks, imps, 4),
        "cpc": _div(spend, clicks, 2), "conv_rate": _div(n, clicks, 4),
        "meta_roas": _div(pval, spend, 2),
        "impressions": None if imps is None else int(imps), "link_clicks": None if clicks is None else int(clicks),
        "meta_purchase_value": None if pval is None else round(pval, 2),
    }


def _row(code: str, sp: dict | None, nc: dict | None, nc_loaded: bool, total_spend: float | None) -> dict:
    n = (int(nc["new_customers"] or 0) if nc else 0) if nc_loaded else None
    rev = (float(nc["revenue"] or 0) if nc else 0.0) if nc_loaded else None
    m = _metrics(_f(sp["spend"]) if sp else None, _f(sp["impressions"]) if sp else None, _f(sp["link_clicks"]) if sp else None,
                 _f(sp["purchase_value"]) if sp else None, n, rev, total_spend)
    return {"code": code, "country": country_name(code), **m}


def build(src, w: q.WindowSpec, meta: q.DataMeta) -> dict:
    """The Countries payload for window `w`. A table with no data yet answers loaded=false with the reason (the page shows it instead of zeros)."""
    spans = src.spans()
    if spans["spend"] is None:
        return empty_payload()
    req_lo, req_hi = w.span(meta)
    s_first, s_last = spans["spend"]
    nc_span = spans["nc"]
    nc_loaded = nc_span is not None
    lo, hi = max(req_lo, s_first), min(req_hi, s_last)
    if nc_loaded:
        lo, hi = max(lo, nc_span[0]), min(hi, nc_span[1])
    info = window_info(w, meta, spans)
    base = {"channel": "meta", "view": "countries", "window": info, "window_info": info, "compare_info": None, "nc_loaded": nc_loaded,
            "coverage": {"spend_first": _iso(s_first), "spend_last": _iso(s_last), "nc_first": _iso(nc_span[0]) if nc_loaded else None, "nc_last": _iso(nc_span[1]) if nc_loaded else None},
            "requested": {"from": _iso(req_lo), "to": _iso(req_hi)}}
    if lo > hi:
        covers = f"{_day_label(s_first)} to {_day_label(s_last)}"
        return {**base, "loaded": False, "message": f"No country data in this window. The Meta country breakdown covers {covers}.", "rows": [], "total": None, "range": None}
    notes = []
    if lo > req_lo:
        notes.append(f"The Meta country breakdown starts {_day_label(s_first)}"
                     + (f" and new customers by country from {_day_label(nc_span[0])}" if nc_loaded and nc_span[0] > s_first else "")
                     + f", so figures cover {_day_label(lo)} onward; days before that are blank, not zero.")
    if hi < req_hi:
        notes.append(f"Country data runs through {_day_label(hi)}.")
    if not nc_loaded:
        notes.append("New customers by country have not loaded yet, so those columns are blank.")
    spend = {r["country"]: r for r in src.spend(lo, hi)}
    nc = {r["country"]: r for r in src.nc(lo, hi)} if nc_loaded else {}
    codes = set(spend) | {c for c, r in nc.items() if float(r.get("revenue") or 0) != 0 or int(r.get("new_customers") or 0) != 0}
    t_spend = sum(float(r["spend"]) for r in spend.values() if r["spend"] is not None) if spend else None
    rows = [_row(c, spend.get(c), nc.get(c), nc_loaded, t_spend) for c in codes]
    rows.sort(key=lambda r: (-(r["spend"] if r["spend"] is not None else -1.0), -(r["nc_revenue"] or 0.0), r["country"]))

    def tsum(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) if vals else None
    n_tot = tsum("new_customers") if nc_loaded else None
    rev_tot = tsum("nc_revenue") if nc_loaded else None
    total = {"code": None, "country": "Total",
             **_metrics(t_spend, tsum("impressions"), tsum("link_clicks"), tsum("meta_purchase_value"), n_tot, rev_tot, t_spend)}
    return {**base, "loaded": True, "range": {"from": _iso(lo), "to": _iso(hi), "days": (hi - lo).days + 1}, "notes": notes, "rows": rows, "total": total, "count": len(rows)}
