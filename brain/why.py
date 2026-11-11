"""`why`: walk the metric tree in brain/why_tree.py live against the analytics views and keep asking why until a concrete cause (or an honest residual) is reached.

For a target window and a comparison window it computes every node, follows only the branches that moved materially (and persistently: a one-day spike does not drive the cascade), looks at the
daily / weekly / monthly cuts, ranks contributors, looks up dated change events around each break date, and runs a control on every candidate cause before naming it (rule 13).
Arrows are accounting ('of which'), never 'because'. Confidence words: observed | consistent with | supported by control | refuted | untested.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any, Callable

from . import why_stats as S
from . import why_tree as T
from .sqlguard import check_select
from .why_questions import PAST_QUESTIONS

PRODUCTS = {"vita-bull": "vita-bull", "vitabull": "vita-bull", "vital bull": "vita-bull", "vita bull": "vita-bull", "beef-liver": "beef-liver", "beefliver": "beef-liver", "beef liver": "beef-liver",
            "boneflex": "boneflex", "vita-belle": "vita-belle", "vitabelle": "vita-belle", "vital belle": "vita-belle", "vital-flow": "vital-flow", "vitalflow": "vital-flow", "vital flow": "vital-flow"}
ALIASES = {"nc_cpa": "nc_cpa", "cpa": "nc_cpa", "nc cpa": "nc_cpa", "cac": "nc_cpa", "roas": "nc_roas", "nc roas": "nc_roas", "new customer revenue": "nc_revenue", "nc revenue": "nc_revenue", "repeat": "repeat_revenue",
           "ctr": "ctr", "cpm": "cpm", "cpc": "cpc", "conversion": "conversion", "cvr": "conversion", "aov": "nc_aov", "spend": "spend", "revenue": "revenue"}
DEN_NAMES = {"spend": "total spend", "nc": "new customers", "clicks": "unique outbound clicks (link clicks when scoped)", "impressions": "impressions", "meta_spend": "Meta spend", "reach_daily": "daily reach (summed)",
             "rep_orders": "repeat orders", "base_nc": "new customers of the prior 180 days", "k_flow_deliv": "flow messages delivered", "k_camp_deliv": "campaign messages delivered", "k_fw_deliv": "messages delivered",
             "so_orders": "new-customer orders", "so_units": "units", "nc_rev": "new-customer revenue"}
SCOPED_FAMILY = {"meta": "sc_meta", "google": "sc_google"}
HORIZON = 34
TOP_N = 40


class BudgetExceeded(Exception):
    pass


class WhyError(ValueError):
    pass


def _d(v, name):
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise WhyError(f"{name} must be a date like 2026-10-31") from None


def _asd(v):
    if isinstance(v, dt.datetime):
        return v.date()
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def fmt_signed(v):
    return "n/a" if v is None else f"{v:+,.3g}"


def pct(x):
    return "n/a" if x is None else ("flat" if abs(x) < 0.0005 else f"{x * 100:+.0f}%" if abs(x) >= 0.1 else f"{x * 100:+.1f}%")


def fmt(v, unit):
    if v is None:
        return "n/a"
    if unit == "usd":
        return f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"
    if unit == "pct":
        return f"{v * 100:.2f}%"
    if unit == "count":
        return f"{v:,.0f}"
    return f"{v:,.3g}" if abs(v) < 100 else f"{v:,.0f}"


def _ev(expr, m):
    if not expr:
        return None
    try:
        v = eval(expr, {"__builtins__": {}}, m)                      # noqa: S307  (expressions are repo constants, names are measure keys)
    except (TypeError, ZeroDivisionError, NameError, KeyError):
        return None
    return v


class Why:
    def __init__(self, run: Callable[[str, int], dict], kn=None, max_steps: int = 40, time_budget_s: float = 24.0):
        self.run, self.kn = run, kn
        self.max_steps, self.steps, self.t0, self.time_budget = max_steps, 0, time.time(), time_budget_s
        self.cache: dict[str, list[dict]] = {}
        self.queries: list[str] = []
        self.notes: list[str] = []
        self.next_questions: list[dict] = []
        self.gaps: list[dict] = []
        self.views: set[str] = set()
        self.coverage: dict[str, dict] = {}

    # ------------------------------------------------------------ database
    def q(self, sql: str, limit: int = 1000) -> list[dict]:
        sql = sql.strip()
        if sql in self.cache:
            return self.cache[sql]
        if self.steps >= self.max_steps or time.time() - self.t0 > self.time_budget:
            raise BudgetExceeded()
        check_select(sql)
        self.steps += 1
        self.queries.append(sql)
        res = self.run(sql, limit)
        cols = res["columns"]
        rows = [dict(zip(cols, r)) for r in res["rows"]]
        self.cache[sql] = rows
        return rows

    def has(self, *views) -> bool:
        return all(v in self.views for v in views)

    # ------------------------------------------------------------ setup
    def setup(self, a: dict) -> None:
        self.metric = ALIASES.get(re.sub(r"[^a-z_ ]+", "", str(a.get("metric") or "revenue").lower().strip()), None) or str(a.get("metric") or "revenue").lower().strip()
        if self.metric not in T.BY_ID or T.BY_ID[self.metric]["kind"] != "scalar":
            raise WhyError(f"metric must be one of: {', '.join(n['id'] for n in T.NODES if n['kind'] == 'scalar')}")
        self.max_depth = int(a.get("max_depth") or 10)
        self.grain = a.get("grain") or "auto"
        if self.grain not in ("auto", "day", "week", "month"):
            raise WhyError("grain must be auto, day, week or month")
        prod = a.get("product")
        self.product = None
        if prod:
            self.product = PRODUCTS.get(str(prod).lower().strip())
            if not self.product:
                raise WhyError("product must be vita-bull, beef-liver, boneflex, vita-belle or vital-flow")
        self.platform = (a.get("platform") or "").lower() or None
        if self.platform not in (None, "meta", "google"):
            raise WhyError("platform must be meta or google")
        if self.product and self.platform == "google":
            raise WhyError("Google campaigns have no product split")
        self.scoped = bool(self.product or self.platform)
        self.scope_fam = SCOPED_FAMILY[self.platform or "meta"]
        self.prod_clause = f" and d.product_slug = '{self.product}'" if self.product else ""
        try:
            self.views = {r["table_name"] for r in self.q(T.VIEWS_SQL)}
            self.coverage = {r["source"]: {**r, "first_day": _asd(r["first_day"]) if r["first_day"] else None, "last_day": _asd(r["last_day"]) if r["last_day"] else None} for r in self.q(T.COVERAGE_SQL)}
        except BudgetExceeded:
            raise WhyError("max_steps too small to start") from None
        end = a.get("end")
        if end is None:
            last = [r["last_day"] for k, r in self.coverage.items() if k.startswith(("orders", "platform", "meta_ad_daily")) and r["last_day"]]
            if not last:
                raise WhyError("end is required (data_coverage is empty)")
            end = min(last) if last else None
        self.end = _d(end, "end")
        self.start = _d(a["start"], "start") if a.get("start") else self.end - dt.timedelta(days=6)
        if self.end < self.start:
            raise WhyError("end is before start")
        self.compare = a.get("compare") or "prior_period"
        try:
            self.cstart, self.cend = S.compare_window(self.compare, self.start, self.end, _d(a["compare_start"], "compare_start") if a.get("compare_start") else None,
                                                      _d(a["compare_end"], "compare_end") if a.get("compare_end") else None)
        except ValueError as e:
            raise WhyError(str(e)) from None
        self.grains = {"day", "week", "month"} if self.grain == "auto" else {self.grain}
        self.periods = [("cur", self.start, self.end, {}), ("cmp", self.cstart, self.cend, {})] + S.grain_periods(self.end, self.grains)
        self.pmeta = {lab: (s, e, m) for lab, s, e, m in self.periods}
        self.fam_cache: dict[str, dict] = {}
        self.findings: list[dict] = []
        self.budget_hit = False

    def pvals(self, periods=None) -> str:
        return ", ".join(f"('{lab}', date '{s}', date '{e}')" for lab, s, e, _ in (periods or self.periods))

    def fmt_sql(self, sql: str, periods=None, **kw) -> str:
        return sql.format_map({"periods": self.pvals(periods), "prod": self.prod_clause, "hstart": self.end - dt.timedelta(days=HORIZON), "end": self.end, "start": self.start, "cstart": self.cstart,
                               "cend": self.cend, **kw})

    # ------------------------------------------------------------ scalar measures
    def family(self, fam: str) -> dict | None:
        if self.scoped and fam in ("orders", "spend", "reach"):
            fam = self.scope_fam
        if fam in self.fam_cache:
            return self.fam_cache[fam]
        spec = T.FAMILIES[fam]
        if not self.has(*spec["needs"]):
            self.fam_cache[fam] = None
            return None
        rows = self.q(self.fmt_sql(spec["sql"]))
        out = {r["p"]: {k: (float(v) if isinstance(v, (int, float)) or hasattr(v, "quantize") else v) for k, v in r.items() if k != "p"} for r in rows}
        self.fam_cache[fam] = out
        return out

    def measures(self, node: dict) -> dict[str, dict] | None:
        fams = sorted({("sc" if self.scoped else T.MEASURE_FAMILY[m]) if self.scoped else T.MEASURE_FAMILY[m] for m in T._measures_of(node) if m in T.MEASURE_FAMILY or self.scoped})
        merged: dict[str, dict] = {}
        for f in {self.scope_fam if self.scoped and x == "sc" else x for x in fams}:
            data = self.family(f)
            if data is None:
                return None
            for p, row in data.items():
                merged.setdefault(p, {}).update(row)
        return merged

    def ndays(self, p) -> int:
        s, e, _ = self.pmeta[p]
        return (e - s).days + 1

    def value(self, node, m: dict | None, p: str):
        if m is None or p not in m:
            return None, None, None
        mm = m[p]
        num = _ev(node.get("num"), mm)
        den = _ev(node.get("den"), mm) if node.get("den") else None
        scale = node.get("scale", 1)
        if node.get("den"):
            return (None if num is None or not den else scale * num / den), num, den
        return (None if num is None else num / self.ndays(p)), num, None

    def series(self, node, m, prefix: str, only: list[str] | None = None):
        out = []
        for lab, s, e, meta in self.periods:
            if lab.startswith(prefix):
                v, n, d = self.value(node, m, lab)
                out.append((lab, v, n, d, meta))
        return out

    # ------------------------------------------------------------ assess one scalar node
    def assess(self, node, m) -> dict:
        v0, n0, d0 = self.value(node, m, "cmp")
        v1, n1, d1 = self.value(node, m, "cur")
        mat = node.get("mat") or {}
        res = {"values": {"cur": v1, "cmp": v0, "delta": None if v0 is None or v1 is None else v1 - v0, "rel": None, "numerator_cur": n1, "numerator_cmp": n0, "denominator_cur": d1, "denominator_cmp": d0},
               "unit": node["unit"], "denominator": self.den_text(node), "reasons": []}
        if v0 is None or v1 is None:
            res["status"] = "no_data"
            res["reasons"].append("no rows or a zero denominator in one of the windows")
            return res
        rel = (v1 - v0) / abs(v0) if v0 else (math.inf if v1 else 0.0)
        res["values"]["rel"] = None if math.isinf(rel) else rel
        res["rel_raw"] = rel
        mind = mat.get("min_den")
        if node.get("den") and mind and min(d0 or 0, d1 or 0) < mind:
            res["status"] = "low_volume"
            res["reasons"].append(f"denominator below {mind:,} in a window ({self.den_text(node)})")
            return res
        if abs(rel) < mat.get("rel", 0.06) or abs(v1 - v0) < mat.get("abs", 0):
            res["status"] = "flat"
            res["reasons"].append(f"moved {pct(rel)}, below the {mat.get('rel', 0.06) * 100:.0f}% / absolute threshold")
            return res
        res["status"] = "material"
        return res

    def den_text(self, node) -> str:
        if node.get("den"):
            d = DEN_NAMES.get(node["den"], node["den"])
            if self.scoped:
                d = d.replace("unique outbound clicks (link clicks when scoped)", "link clicks")
            else:
                d = d.replace(" (link clicks when scoped)", "")
            return f"per {d}"
        return f"per day (average over the window; {self.ndays('cur')} days)"

    # ------------------------------------------------------------ grains
    def grain_block(self, node, m, rel_floor: float) -> dict:
        out: dict[str, Any] = {}
        if m is None:
            return out
        spike_week_out = False
        if "day" in self.grains:
            ser = self.series(node, m, "d:")
            a = S.analyse_series([(l[2:], v) for l, v, *_ in ser], "day", rel_floor)
            last7 = ser[-7:]
            prev = ser[-35:-7]
            sm = self._ratio(node, last7), self._ratio(node, prev)
            summary = None if sm[0] is None or not sm[1] else {"latest": sm[0], "baseline": sm[1], "rel": sm[0] / sm[1] - 1, "what": "last 7 days vs the 28 days before"}
            cur_idx = [i for i, (l, *_r) in enumerate(ser) if self.start <= dt.date.fromisoformat(l[2:]) <= self.end]
            lfl = S.weekday_like_for_like([((n if node.get("den") else v), (d if node.get("den") else 1.0)) for _, v, n, d, _m in ser], cur_idx) if cur_idx and len(ser) >= 14 else None
            out["day"] = {"series": [[l[2:], v] for l, v, *_ in ser], **a, "summary": summary, "weekday_like_for_like": lfl,
                          "note": "same weekday in the 4 prior weeks, ratio of sums" if lfl else None}
        if "week" in self.grains:
            ser = [x for x in self.series(node, m, "w:")]
            full = [x for x in ser if not x[4].get("partial")]
            a = S.analyse_series([(l[2:], v) for l, v, *_ in full], "week", rel_floor)
            summary = None
            vals = [v for _, v, *_ in full if v is not None]
            if len(vals) >= 5:
                base = S.median(vals[-5:-1])
                summary = {"latest": vals[-1], "baseline": base, "rel": (vals[-1] / base - 1) if base else None, "what": "last complete week vs the median of the 4 weeks before"}
            out["week"] = {"series": [[l[2:], v, bool(meta.get("partial"))] for l, v, _n, _d_, meta in ser], **a, "summary": summary}
            spike_week_out = a["shape"] in ("step", "drift") or a["break"] is not None
        if "month" in self.grains:
            same = self.series(node, m, "mm:")
            closed = self.series(node, m, "m:")
            a = S.analyse_series([(l[3:], v) for l, v, *_ in same], "month", rel_floor)
            vals = [v for _, v, *_ in same if v is not None]
            summary = None
            if len(vals) >= 3:
                base = S.median(vals[:-1][-6:])
                summary = {"latest": vals[-1], "baseline": base, "rel": (vals[-1] / base - 1) if base else None, "what": "month to date vs the same days of earlier months (median)"}
            out["month"] = {"series_same_days": [[l[3:], v] for l, v, *_ in same], "series_closed_months": [[l[2:], v] for l, v, *_ in closed], **a, "summary": summary,
                            "note": "same days of the month in every month, so billing-cycle and month-length effects are not read as a trend"}
        d = out.get("day")
        out["spike_only"] = bool(d and d["shape"] == "spike" and not spike_week_out and "week" in out)
        out["disagreement"] = self.disagreement(out, rel_floor)
        out["when_it_broke"] = {g: {"break": out[g].get("break"), "shape": out[g]["shape"], "direction": out[g].get("break_direction")} for g in ("day", "week", "month") if g in out}
        return out

    def _ratio(self, node, ser):
        n = sum(x[2] for x in ser if x[2] is not None)
        if node.get("den"):
            d = sum(x[3] for x in ser if x[3] is not None)
            return node.get("scale", 1) * n / d if d else None
        return n / max(1, len(ser)) if ser else None

    def disagreement(self, out, rel_floor) -> str | None:
        parts = {}
        for g in ("day", "week", "month"):
            sm = (out.get(g) or {}).get("summary")
            if sm and sm.get("rel") is not None and abs(sm["rel"]) >= rel_floor:
                parts[g] = sm["rel"]
        if len(parts) >= 2 and len({x > 0 for x in parts.values()}) > 1:
            return "grains disagree: " + ", ".join(f"{g} {pct(r)} ({out[g]['summary']['what']})" for g, r in parts.items())
        return None

    # ------------------------------------------------------------ tree walk
    def walk(self, nid: str, depth: int, parent: dict | None, path: list[dict], sign_chain: float = 1.0) -> dict:
        node = T.BY_ID[nid]
        base = {"id": nid, "label": node["label"], "formula": node.get("formula"), "kind": node["kind"], "unit": node["unit"],
                "past_questions": self.past_questions(node), "settles_with": node.get("settles_with")}
        if node["kind"] == "gap":
            self.gaps.append({"node": nid, **node["data_gap"]})
            return {**base, "status": "data_gap", "data_gap": node["data_gap"], "known_causes": self.kc_block(node, None, None)}
        if node["kind"] == "mix":
            return self.walk_mix(node, base, depth, parent, path)
        if node["kind"] == "routing":
            return self.walk_routing(node, base, depth, parent, path)
        if self.scoped and node.get("account_only"):
            return {**base, "status": "data_gap", "data_gap": {"why": "this node needs actual orders, which have no product or platform split in analytics", "where": "actual orders are account level only (analytics.orders_daily)"}}
        if node.get("needs_views") and not self.has(*node["needs_views"]):
            g = {"why": node["gap_text"], "where": node["gap_where"], "runs_when_views_exist": node["needs_views"]}
            self.gaps.append({"node": nid, **g})
            return {**base, "status": "data_gap", "data_gap": g, "known_causes": self.kc_block(node, None, None)}
        try:
            m = self.measures(node)
        except BudgetExceeded:
            self.budget_hit = True
            return {**base, "status": "not_run", "reasons": ["query budget (max_steps) used up"]}
        if m is None:
            return {**base, "status": "data_gap", "data_gap": {"why": "source view missing", "where": ", ".join(T.FAMILIES.get(f, {}).get("needs", []) for f in [])}}
        a = self.assess(node, m)
        mat = node.get("mat") or {}
        rel_floor = mat.get("rel", 0.06)
        res = {**base, **a, "coverage_warnings": self.coverage_warnings(node, m)}
        if a["status"] in ("material", "flat"):
            res["grains"] = self.grain_block(node, m, rel_floor)
            res["_break"] = self.break_date(res["grains"])
            if a["status"] == "material" and res["grains"].get("spike_only"):
                res["status"] = "spike_only"
                res["reasons"].append("the move is a one-day spike: it does not persist for 3+ days and does not show at the weekly grain")
        res["known_causes"] = self.kc_block(node, a, res.get("grains"))
        here = {"node": nid, "label": node["label"], "rel": a["values"]["rel"], "cur": a["values"]["cur"], "cmp": a["values"]["cmp"], "unit": node["unit"], "denominator": a["denominator"]}
        res["_path"] = path + [here]
        res["children"], res["not_followed"] = [], []
        if nid == "nc_roas" and res["status"] == "material":
            res["roas_split"] = self.roas_split(res)
        if res["status"] != "material" or depth >= self.max_depth:
            if res["status"] == "material":
                res["reasons"].append("max_depth reached")
            return res
        kids = []
        for ch in node["children"]:
            cn = T.BY_ID[ch["id"]]
            if cn["kind"] == "scalar" and ch["role"] == "part":
                cm = None
                try:
                    cm = self.measures(cn)
                except BudgetExceeded:
                    self.budget_hit = True
                if cm is None or (self.scoped and cn.get("account_only")):
                    kids.append((0.0, ch, None))
                    continue
                ca = self.assess(cn, cm)
                share = self.contribution(node, cn, ch, res["values"], ca["values"], a["values"])
                kids.append((abs(share or 0), ch, (ca, share)))
            else:
                kids.append((-1.0, ch, None))
        kids.sort(key=lambda x: -x[0])
        for _, ch, pre in kids:
            child = self.walk(ch["id"], depth + 1, res, res["_path"])
            if pre and pre[1] is not None:
                child["share_of_parent_change"] = pre[1]
                if child.get("_path"):
                    child["_path"][-1]["share_of_parent"] = pre[1]
            st = child.get("status")
            if st == "material" or (st == "data_gap" and child["kind"] != "scalar") or (st == "data_gap" and ch["role"] == "info"):
                res["children"].append(child)
            else:
                res["not_followed"].append({"id": child["id"], "label": child["label"], "status": st, "rel": (child.get("values") or {}).get("rel"), "reasons": child.get("reasons")})
        return res

    def break_date(self, grains) -> dt.date | None:
        for g in ("day", "week", "month"):
            b = (grains.get(g) or {}).get("break")
            if b and (grains[g].get("shape") in ("step", "drift")):
                try:
                    return dt.date.fromisoformat(b[:10])
                except ValueError:
                    pass
        return None

    def roas_split(self, res) -> dict | None:
        """NC ROAS = NC AOV / NC CPA, so ln(ROAS change) = ln(AOV change) - ln(CPA change): an exact log split."""
        an, cn = T.BY_ID["nc_aov"], T.BY_ID["nc_cpa"]
        ma, mc = self.measures(an), self.measures(cn)
        a1, a0 = self.value(an, ma, "cur")[0], self.value(an, ma, "cmp")[0]
        c1, c0 = self.value(cn, mc, "cur")[0], self.value(cn, mc, "cmp")[0]
        r1, r0 = res["values"]["cur"], res["values"]["cmp"]
        try:
            la, lc, lr = math.log(a1 / a0), -math.log(c1 / c0), math.log(r1 / r0)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        ra, rc, rr = a1 / a0 - 1, c1 / c0 - 1, r1 / r0 - 1
        word = lambda x, thr: "flat" if abs(x) < thr else pct(x)  # noqa: E731
        fa, fc = word(ra, an["mat"]["rel"]), word(rc, cn["mat"]["rel"])
        return {"label": "accounting (exact log split), not cause", "log_effects": {"aov": la, "cpa": lc, "roas": lr, "sum_check": la + lc - lr}, "relative": {"aov": ra, "cpa": rc, "roas": rr},
                "text": f"ROAS {pct(rr)}: AOV {fa}, CPA {fc}", "chain": f"ROAS {'down' if rr < 0 else 'up'}: " + (f"AOV effect {fa}" if fa != "flat" else "AOV flat") + f", CPA {fc}" + (" (so the AOV branch is read, not CPA)" if fc == "flat" and fa != "flat" else ""),
                "descend_into": "nc_aov" if fc == "flat" and fa != "flat" else ("nc_cpa" if fa == "flat" and fc != "flat" else "both")}

    def contribution(self, parent, child, ch, pv, cv, av) -> float | None:
        link = parent.get("link")
        try:
            if link == "add":
                dn = (cv["numerator_cur"] - cv["numerator_cmp"])
                tot = (av["numerator_cur"] - av["numerator_cmp"])
                return dn / tot if tot else None
            if link == "mult":
                lc = math.log(cv["cur"] / cv["cmp"]) * ch["sign"]
                lp = math.log(av["cur"] / av["cmp"])
                return lc / lp if lp else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return None

    def past_questions(self, node) -> list[dict]:
        ids = PAST_QUESTIONS.get(node.get("pq") or "", [])
        out = []
        by = {str(it.get("id")): it for it in (self.kn.questions if self.kn else [])}
        for i in ids[:3] if len(ids) > 3 else ids:
            it = by.get(i)
            if it:
                ans = it.get("answer")
                out.append({"id": i, "date": it.get("date"), "verbatim": (it.get("verbatim") or "")[:300], "found": (ans if isinstance(ans, str) else str(ans))[:420], "status": it.get("status")})
        return out

    def kc_block(self, node, assess, grains) -> list[dict]:
        out = []
        direction = None
        if assess and assess["values"].get("rel") is not None and assess["status"] == "material":
            direction = "up" if assess["values"]["rel"] > 0 else "down"
        for k in node.get("known_causes", []):
            between = k.get("between")
            overlaps = None
            if between:
                s0 = _d(between[0], "x"); e0 = _d(between[1], "x") if between[1] else dt.date.max
                overlaps = not (e0 < self.start or s0 > self.end)
            matches = bool(assess and assess["status"] == "material" and (k["direction"] in (None, direction)) and (overlaps in (None, True)))
            out.append({**k, "overlaps_window": overlaps, "matches_current_pattern": matches})
        return out

    def coverage_warnings(self, node, m) -> list[str]:
        w = []
        for p, label in (("cur", "target"), ("cmp", "comparison")):
            mm = (m or {}).get(p) or {}
            nd = self.ndays(p)
            for key, name in (("o_days", "orders"), ("meta_days", "Meta spend"), ("google_days", "Google spend"), ("r_days", "Meta reach")):
                if key in mm and key in T._measures_of({"num": node.get("num"), "den": node.get("den")}) or False:
                    pass
            used = T._measures_of({"num": node.get("num"), "den": node.get("den")})
            for key, name, meas in (("o_days", "orders_daily", {"revenue", "nc_rev", "rep_rev", "nc", "orders", "rep_orders", "base_nc"}), ("meta_days", "Meta spend", {"meta_spend", "spend", "meta_own_rev"}),
                                    ("r_days", "meta_reach", {"impressions", "clicks", "reach_daily"})):
                if key in mm and used & meas and mm[key] < nd:
                    w.append(f"{label} window: {name} has {int(mm[key])} of {nd} days loaded")
        return sorted(set(w))

    # ------------------------------------------------------------ mix nodes
    def group_rows(self, group: str, periods=None, keys=None) -> list[dict]:
        sql = self.fmt_sql(T.group_sql(group, TOP_N, keys), periods)
        return self.q(sql)

    def walk_mix(self, node, base, depth, parent, path) -> dict:
        g = T.MIX_GROUPS[node["group"]]
        if self.product and node["group"] in ("product",):
            return {**base, "status": "skipped", "reasons": ["a product filter is set"]}
        if g["source"] == "meta" and self.platform == "google":
            return {**base, "status": "skipped", "reasons": ["platform is google"]}
        if g["source"] == "google" and (self.scoped and self.platform != "google" or self.product):
            return {**base, "status": "skipped", "reasons": ["Google branch only for the account or platform=google"]}
        if not self.has(*g["needs"]):
            bd = g["source"] == "breakdown"
            gp = {"why": ("Meta breakdowns (audience segment, age, gender, country, placement, device, hour) are not stored yet" if bd else f"{', '.join(g['needs'])} not exposed in the analytics schema yet"),
                  "where": ("Ads Manager breakdowns / Graph API insights breakdowns (feed proposed: analytics.meta_breakdown_daily)" if bd else "landing_page_ads / landing_pages (feat/lp-copy-store, PR #95)"), "runs_when_views_exist": g["needs"]}
            self.gaps.append({"node": node["id"], **gp})
            return {**base, "status": "data_gap", "data_gap": gp}
        first_day = (self.coverage.get("meta_ad_daily") or {}).get("first_day")
        if g["source"] == "shopify" and self.scoped:
            return {**base, "status": "skipped", "reasons": ["Shopify orders have no product-by-platform scope here: run without product / platform"]}
        if g["source"] == "breakdown" and (self.product or self.platform == "google"):
            return {**base, "status": "skipped", "reasons": ["Meta breakdowns have no product split (ad set grain) and are Meta only"]}
        if g["source"] == "meta" and first_day and min(self.start, self.cstart) < first_day:
            return {**base, "status": "data_gap", "data_gap": {"why": f"ad-level data starts {first_day}; a window starts earlier", "where": "analytics.meta_campaign_daily_history (campaign grain only)"}}
        if g["source"] == "google":
            up = dt.date(2025, 5, 13)
            if min(self.start, self.cstart) < up:
                base["coverage_note"] = f"Upstack's Google attribution only exists from {up}: shares / ROAS before that are not comparable"
        try:
            rows = self.group_rows(node["group"], [p for p in self.periods if p[0] in ("cur", "cmp")])
        except BudgetExceeded:
            self.budget_hit = True
            return {**base, "status": "not_run", "reasons": ["query budget (max_steps) used up"]}
        groups: dict[str, dict] = {}
        labels = {}
        for r in rows:
            k = str(r["k"])
            labels[k] = r["lbl"]
            groups.setdefault(k, {"cur": {}, "cmp": {}})[r["p"]] = {x: float(r[x] or 0) for x in ("spend", "impressions", "clicks", "meta_purchases", "meta_revenue", "nc", "nc_rev", "live_ads")}
        for gv in groups.values():
            for w in ("cur", "cmp"):
                if not gv[w]:
                    gv[w] = {x: 0.0 for x in ("spend", "impressions", "clicks", "meta_purchases", "meta_revenue", "nc", "nc_rev", "live_ads")}
        rate = {"roas": ("nc_rev", "spend", 1.0), "conv": ("nc", "clicks", 1.0), "cpm": ("spend", "impressions", 1000.0), "meta_roas": ("meta_revenue", "spend", 1.0), "meta_conv": ("meta_purchases", "clicks", 1.0), "aov": ("nc_rev", "nc", 1.0)}[node["rate"]]
        ss = S.shift_share(groups, rate[0], rate[1], rate[2])
        if ss is None:
            return {**base, "status": "no_data", "reasons": ["no rows or a zero denominator in a window"]}
        tot_sp = {w: sum(g[w]["spend"] for g in groups.values()) for w in ("cur", "cmp")}
        shop = g["source"] == "shopify"
        guard = (lambda w: max(0.01 * tot_sp[w], 15.0)) if shop else (lambda w: max(0.005 * tot_sp[w], 100.0))  # noqa: E731
        table = []
        for k, gv in groups.items():
            r = ss["rows"].get(k)
            if not r:
                continue
            c, p = gv["cur"], gv["cmp"]
            table.append({"key": k, "label": labels[k], "kind": r["kind"], "spend_cur": c["spend"], "spend_cmp": p["spend"], "spend_share_cur": c["spend"] / tot_sp["cur"] if tot_sp["cur"] else None,
                          "spend_share_cmp": p["spend"] / tot_sp["cmp"] if tot_sp["cmp"] else None,
                          "nc_roas_upstack_cur": c["nc_rev"] / c["spend"] if c["spend"] else None, "nc_roas_upstack_cmp": p["nc_rev"] / p["spend"] if p["spend"] else None,
                          "meta_roas_cur": c["meta_revenue"] / c["spend"] if c["spend"] else None, "meta_roas_cmp": p["meta_revenue"] / p["spend"] if p["spend"] else None,
                          "cpa_upstack_cur": c["spend"] / c["nc"] if c["nc"] else None, "cpa_upstack_cmp": p["spend"] / p["nc"] if p["nc"] else None,
                          "conversion_cur": c["nc"] / c["clicks"] if c["clicks"] else None, "conversion_cmp": p["nc"] / p["clicks"] if p["clicks"] else None,
                          "live_ads_cur": c["live_ads"], "rate_cur": r["rate_cur"], "rate_cmp": r["rate_cmp"], "contribution": r["contribution"],
                          "share_of_parent_change": (r["contribution"] / ss["delta"]) if ss["delta"] else None})
        if shop:
            for t in table:
                t["orders_cur"], t["orders_cmp"] = t.pop("spend_cur"), t.pop("spend_cmp")
                t["share_of_orders_cur"], t["share_of_orders_cmp"] = t["spend_share_cur"], t["spend_share_cmp"]
                t["aov_cur"], t["aov_cmp"] = t["rate_cur"], t["rate_cmp"]
                for kx in ("nc_roas_upstack_cur", "nc_roas_upstack_cmp", "meta_roas_cur", "meta_roas_cmp", "cpa_upstack_cur", "cpa_upstack_cmp", "conversion_cur", "conversion_cmp", "live_ads_cur"):
                    t.pop(kx, None)
        real = [t for t in table if t["key"] != "~other" and not t["key"].startswith("(")]
        movers_share = sorted([t for t in real if max(t.get("spend_cur", t.get("orders_cur", 0)), t.get("spend_cmp", t.get("orders_cmp", 0))) >= guard("cur")], key=lambda t: -abs((t["spend_share_cur"] or 0) - (t["spend_share_cmp"] or 0)))[:3]
        movers_rate = sorted([t for t in real if t.get("spend_cur", t.get("orders_cur", 0)) >= guard("cur") and t.get("spend_cmp", t.get("orders_cmp", 0)) >= guard("cmp") and t["rate_cur"] is not None and t["rate_cmp"]],
                             key=lambda t: -abs(t["rate_cur"] / t["rate_cmp"] - 1))[:3]
        untagged = next((t for t in table if t["key"].startswith("(")), None)
        lead = max(real or table, key=lambda t: abs(t["contribution"])) if table else None
        sign = 1 if ss["delta"] >= 0 else -1
        lead_share = (lead["contribution"] * sign) / abs(ss["delta"]) if lead and ss["delta"] else 0.0
        floor = node.get("mat", {}).get("share", 0.5)
        mixflag = max([abs((t["spend_share_cur"] or 0) - (t["spend_share_cmp"] or 0)) for t in movers_share] or [0.0])
        rel = ss["delta"] / abs(ss["cmp"]) if ss["cmp"] else None
        parent_floor = (T.BY_ID[node["parent"]].get("mat") or {}).get("rel", 0.06)
        market = None
        if node["rate"] == "cpm":
            cells = [t for t in real if t.get("spend_cur", 0) >= guard("cur") and t.get("spend_cmp", 0) >= guard("cmp") and t["rate_cmp"]]
            if len(cells) >= 2 and all(t["rate_cur"] / t["rate_cmp"] - 1 >= parent_floor for t in cells):
                market = {"cells": [t["key"] for t in cells], "claim": f"CPM rose {pct(min(t['rate_cur'] / t['rate_cmp'] - 1 for t in cells))} or more in every {node['group']} cell at once: a market-wide pattern, not a mix effect"}
        status = "flat"
        if rel is not None and abs(rel) >= parent_floor * 0.5 and (lead_share >= 0.25 or mixflag >= 0.05 or market):
            status = "material"
        res = {**base, "status": status, "group": node["group"], "group_is": g["what"], "rate": node["rate"], "basis": "Meta-reported and Upstack new customers shown as separate columns; the split itself uses the Upstack any-click basis (never mixed)" if g["source"] == "meta" else
               "Google's own conversions are a separate basis (never added to Meta's); the split uses Upstack-attributed new customers",
               "total": {"cur": ss["cur"], "cmp": ss["cmp"], "delta": ss["delta"], "rel": rel, "unit": {"roas": "ratio", "conv": "pct", "cpm": "usd", "meta_roas": "ratio", "meta_conv": "pct", "aov": "usd"}[node["rate"]], "denominator": f"per {rate[1]} summed over the values"},
               "mix_vs_rate": {"label": "accounting (shift-share), not cause", "mix_effect": ss["mix_effect"], "rate_effect": ss["rate_effect"], "mix_detail": ss["mix_detail"], "sum_check": ss["sum_check"],
                               "delta": ss["delta"]},
               "top_spend_share_movers": movers_share, "top_rate_movers": movers_rate, "untagged": untagged, "lead": lead and {"key": lead["key"], "label": lead["label"], "share_of_parent_change": lead_share,
                                                                                                                    "spend_share_cur": lead["spend_share_cur"], "spend_share_cmp": lead["spend_share_cmp"], "kind": lead["kind"]},
               "weight": "share of new-customer orders (Shopify, gross order totals, new customers only)" if shop else "share of spend",
               "market_wide": market, "values_table": sorted(table, key=lambda t: -(t.get("spend_cur", t.get("orders_cur", 0))))[:12], "known_causes": self.kc_block(node, {"status": status, "values": {"rel": rel}}, None), "children": [], "not_followed": []}
        res["_path"] = path
        res["_ss"], res["_groups"], res["_labels"] = ss, groups, labels
        res["_parent_id"] = node["parent"]
        if status == "material" and depth < self.max_depth:
            try:
                self.mix_candidates(node, res, rate, path, parent)
            except BudgetExceeded:
                self.budget_hit = True
        return res

    # ------------------------------------------------------------ change events
    def events(self) -> list[dict]:
        if getattr(self, "_events", None) is not None:
            return self._events
        ev: list[dict] = []
        self._events = ev

        def run(name):
            spec = T.CHANGE_SQL[name]
            if not self.has(*spec["needs"]):
                return []
            return self.q(self.fmt_sql(spec["sql"]))
        try:
            for r in run("ad_launches"):
                ev.append({"type": "ad_family_launched", "date": _asd(r["fd"]), "label": r["fname"], "family": r["fk"], "product": r["product"], "cluster": r["cluster"], "icp": r["icp"], "pain_point": r["pain_point"],
                           "ads": r["ads"], "spend_in_window": float(r["spend"] or 0)})
            for r in run("adset_moves"):
                k = r["kind"]
                if k in ("launched", "stopped", "restarted"):
                    d = _asd(r["fd"]) if k != "stopped" else _asd(r["ld"]) + dt.timedelta(days=1)
                    ev.append({"type": f"adset_{k}", "date": d, "label": r["nm"], "campaign": r["cn"], "stage": r["stage"], "adset_id": r["adset_id"], "spend_cur": float(r["spend_cur"] or 0), "spend_cmp": float(r["spend_cmp"] or 0)})
                elif k in ("up", "down"):
                    ev.append({"type": f"adset_scaled_{k}", "date": None, "label": r["nm"], "campaign": r["cn"], "stage": r["stage"], "adset_id": r["adset_id"], "spend_cur": float(r["spend_cur"] or 0),
                               "spend_cmp": float(r["spend_cmp"] or 0)})
            for r in run("feed_breaks"):
                ev.append({"type": "feed_or_tracking_break", "date": _asd(r["day"]), "label": f"{r['job']} {r['status']}: {r['error']}"})
            for r in run("landing_page_versions"):
                ev.append({"type": "landing_page_version_changed", "date": _asd(r["day"]), "label": r["url"], "url": r["url"], "title": r["title"], "versions": r["versions"]})
            for r in run("klaviyo_flow_changes"):
                for kind, key in (("created", "created_day"), ("updated", "updated_day")):
                    if r.get(key) and self.end - dt.timedelta(days=HORIZON) <= _asd(r[key]) <= self.end:
                        ev.append({"type": f"klaviyo_flow_{kind}", "date": _asd(r[key]), "label": f"{r['name']} ({r['status']})", "flow_id": r["id"]})
        except BudgetExceeded:
            self.budget_hit = True
        return ev

    @staticmethod
    def event_key(group, e) -> str | None:
        t = e["type"]
        if t == "ad_family_launched":
            return {"family": e["family"], "product": e["product"], "cluster": e["cluster"], "icp": e["icp"], "pain_point": e["pain_point"]}.get(group)
        if t.startswith("adset_") and not t.startswith("adset_scaled"):
            return e.get("stage") if group == "stage" else None
        if t == "landing_page_version_changed":
            return e["url"] if group == "landing_page" else None
        return None

    def match_events(self, group, key, center) -> list[dict]:
        if center is None:
            return []
        out = []
        for e in self.events():
            if e["date"] is None or abs((e["date"] - center).days) > 3:
                continue
            if self.event_key(group, e) == key:
                out.append({k: (str(v) if isinstance(v, dt.date) else v) for k, v in e.items()})
        return out[:6]

    def near_events(self, center) -> list[dict]:
        if center is None:
            return []
        ev = [e for e in self.events() if e["date"] and abs((e["date"] - center).days) <= 3 and e["type"] in ("ad_family_launched", "landing_page_version_changed", "adset_launched")]
        ev.sort(key=lambda e: -(e.get("spend_in_window") or e.get("spend_cur") or 0))
        return [{k: (str(v) if isinstance(v, dt.date) else v) for k, v in e.items()} for e in ev[:6]]

    def enrich_pages(self, events) -> None:
        fams = [e["family"] for e in events if e["type"] == "ad_family_launched" and e.get("family")]
        if not fams or not self.has("landing_page_ads", "landing_pages", "landing_page_versions"):
            return
        lit = ", ".join("'" + f.replace("'", "''") + "'" for f in fams[:6])
        try:
            m = {r["family_key"]: r for r in self.q(T.LP_FOR_ADS_SQL.format(keys=lit))}
        except BudgetExceeded:
            self.budget_hit = True
            return
        for e in events:
            r = m.get(e.get("family"))
            if r:
                e["landing_page"] = {"url": r["url"], "copy_since": str(r["copy_since"]) if r["copy_since"] else None}

    # ------------------------------------------------------------ controls and findings
    def controls(self, group, key, ss, groups, rate, parent, center) -> dict:
        num, den, scale = rate
        e = groups[key]
        tn = {w: sum(g[w][num] for g in groups.values()) for w in ("cur", "cmp")}
        td = {w: sum(g[w][den] for g in groups.values()) for w in ("cur", "cmp")}
        out: dict[str, Any] = {}
        wo = {}
        for w in ("cur", "cmp"):
            d = td[w] - e[w][den]
            wo[w] = scale * (tn[w] - e[w][num]) / d if d > 0 else None
        gap = ss["delta"]
        if wo["cur"] is not None and wo["cmp"] is not None and gap:
            closes = 1 - (wo["cur"] - wo["cmp"]) / gap
            out["counterfactual_exclusion"] = {"parent_change": gap, "parent_change_without_entity": wo["cur"] - wo["cmp"], "gap_closed": max(-1.0, min(1.5, closes)),
                                               "reading": f"without this {group} the change is {fmt_signed(wo['cur'] - wo['cmp'])} instead of {fmt_signed(gap)}: it closes {max(0, closes) * 100:.0f}% of the gap"}
        else:
            out["counterfactual_exclusion"] = None
        other_rate = 0.0
        for k, r in ss["rows"].items():
            if k != key and r["kind"] == "continuing":
                other_rate += r["share_cur"] * (r["rate_cur"] - r["rate_cmp"])
        out["like_for_like"] = {"label": "the values that did not get the change (continuing, same in both windows)", "their_rate_effect": other_rate, "share_of_gap": (other_rate / gap) if gap else None,
                                "holds": (abs(other_rate / gap) < 0.3) if gap else None}
        pp = None
        series = ((parent or {}).get("grains") or {}).get("day", {}).get("series") if parent else None
        if center and series:
            pts = [(dt.date.fromisoformat(d), v) for d, v in series if v is not None]
            pre = [v for d, v in pts if d < center][-14:]
            post = [v for d, v in pts if d >= center][:14]
            if len(pre) >= 5 and len(post) >= 3:
                lf = S.linear_fit(pre)
                mean = sum(pre) / len(pre)
                flat = abs(lf["slope"] * (len(pre) - 1) / mean) < 0.10 if mean else None
                post_m = sum(post) / len(post)
                pp = {"break_date": str(center), "pre_mean": mean, "post_mean": post_m, "rel": (post_m / mean - 1) if mean else None, "pre_trend_flat": flat,
                      "reading": ("flat pre-trend, then a shift" if flat else "pre-trend was not flat: a pre/post read is weak") if flat is not None else None}
        out["pre_post_parent"] = pp
        return out

    @staticmethod
    def label_from(controls) -> tuple[str, str]:
        cf = controls.get("counterfactual_exclusion")
        lfl = controls.get("like_for_like") or {}
        if not cf:
            return "consistent with, untested", "no control could be run (a window has no rows without this entity)"
        closes = cf["gap_closed"]
        if closes < 0.25:
            return "refuted", f"the gap persists without it (it closes only {max(0, closes) * 100:.0f}%)"
        if closes >= 0.5 and lfl.get("holds") is not False:
            return "supported by control", f"removing it closes {closes * 100:.0f}% of the gap and the other values did not move"
        return "consistent with, untested", f"removing it closes {closes * 100:.0f}% of the gap" + ("; the other values moved too" if lfl.get("holds") is False else "")

    def mix_candidates(self, node, res, rate, path, parent) -> None:
        group = node["group"]
        ss, groups, labels = res["_ss"], res["_groups"], res["_labels"]
        delta = ss["delta"]
        sign = 1 if delta >= 0 else -1
        cands: dict[str, list[str]] = {}

        def add(k, why):
            if k in groups and k != "~other":
                cands.setdefault(k, []).append(why)
        lead = res["lead"]
        if lead and lead["share_of_parent_change"] >= 0.25:
            add(lead["key"], "largest contributor to the change")
        for t in res["top_spend_share_movers"][:2]:
            add(t["key"], "spend share mover")
        pbreak = (parent or {}).get("_break")
        evs = self.events()
        if pbreak:
            for e in evs:
                if e["date"] and abs((e["date"] - pbreak).days) <= 3:
                    k = self.event_key(group, e)
                    if k is not None:
                        add(str(k), "dated change event within 3 days of the break")
        keys = list(cands)[:6]
        ent_series: dict[str, list] = {}
        if keys and self.max_steps - self.steps >= 3:
            dayp = S.grain_periods(self.end, {"day"})
            rows_s = self.group_rows(group, dayp, keys)
            shop = T.MIX_GROUPS[group]["source"] == "shopify"
            totals = {r["p"]: float(r["spend"] or 0) for r in rows_s if r["k"] == "~total"}
            for r in rows_s:
                if r["k"] in keys:
                    v = float(r["spend"] or 0)
                    if shop:                                   # a share of that day's orders; days with no orders at all are missing, not zero
                        v = v / totals[r["p"]] if totals.get(r["p"]) else None
                    ent_series.setdefault(str(r["k"]), []).append((dt.date.fromisoformat(r["p"][2:]), v, r))
        out = []
        g = T.MIX_GROUPS[group]
        for k in keys:
            have = {d: v for d, v, _ in ent_series.get(k, [])}
            shop_g = g["source"] == "shopify"
            ser = [(self.end - dt.timedelta(days=34 - i), have.get(self.end - dt.timedelta(days=34 - i), None if shop_g else 0.0), None) for i in range(35)]
            ebreak = None
            if have:
                fb = S.first_break([(str(d), v) for d, v, _ in ser], 28, 0.25 if not shop_g else 0.15, 3)
                if fb:
                    ebreak = ser[fb[0]][0]
            center = ebreak or pbreak
            events = self.match_events(group, k, center)
            by = None
            if not events and g["source"] == "shopify":
                events = self.near_events(center)
                by = "date only: orders carry no ad or page id, so these are candidates, not attribution"
            if events and g["source"] == "shopify":
                self.enrich_pages(events)
            ctl = self.controls(group, k, ss, groups, rate, parent, center)
            label, why = self.label_from(ctl)
            r = ss["rows"].get(k) or {}
            share = (r.get("contribution", 0) * sign / abs(delta)) if delta else None
            e = groups[k]
            tn = sum(gg["cur"][rate[0]] for gg in groups.values()) - e["cur"][rate[0]]
            td = sum(gg["cur"][rate[1]] for gg in groups.values()) - e["cur"][rate[1]]
            rest = rate[2] * tn / td if td else None
            cand = {"entity": {"group": group, "key": k, "label": labels.get(k, k), "what": g["what"], "kind": r.get("kind")}, "why_candidate": cands[k], "share_of_parent_change": share,
                    "spend_share_cur": r.get("share_cur") if rate[1] == "spend" else None, "share_cur": r.get("share_cur"), "share_cmp": r.get("share_cmp"), "rate_cur": r.get("rate_cur"), "rate_cmp": r.get("rate_cmp"), "rate_rest_cur": rest, "break_date": str(ebreak) if ebreak else None,
                    "break_basis": "first day its daily spend left its trailing 28-day baseline and stayed out 3+ days" if ebreak else (f"parent's break {pbreak}" if pbreak else None),
                    "change_events_within_3_days": events, "events_matched_by": by, "controls": ctl, "label": label, "label_reason": why}
            leaf = "a" if events else ("b" if (share or 0) >= 0.5 else None)
            cand["leaf_type"] = leaf
            if leaf:
                self.findings.append({**cand, "node": node["id"], "parent_node": node["parent"], "rate": node["rate"], "mix_vs_rate": res["mix_vs_rate"], "_path": path + [{"node": node["id"], "label": node["label"], "rel": res["total"]["rel"]}]})
            out.append(cand)
        res["candidates"] = out
        if res.get("market_wide"):
            self.findings.append({"entity": {"group": group, "key": "(all cells)", "label": "every cell"}, "node": node["id"], "parent_node": node["parent"], "leaf_type": "c", "label": "consistent with, untested",
                                  "label_reason": "market-wide pattern: every cell moved the same way, so no entity-level control exists", "share_of_parent_change": 1.0, "change_events_within_3_days": [], "controls": {}, "claim": res["market_wide"]["claim"],
                                  "_path": path + [{"node": node["id"], "label": node["label"], "rel": res["total"]["rel"]}]})
        if group == "family" and out:
            self.family_detail(out, pbreak)
            lead_c = next((c for c in out if c["leaf_type"] or (c["share_of_parent_change"] or 0) >= 0.25), None)
            if lead_c:
                self.ad_breakdown_shift(lead_c, pbreak)
        if group == "bd_audience_segment":
            res["audience_pattern"] = self.audience_pattern(res)

    def family_detail(self, cands, pbreak):
        keys = [c["entity"]["key"] for c in cands if c["leaf_type"] or (c["share_of_parent_change"] or 0) >= 0.25][:3]
        if not keys:
            return
        lit = ", ".join("'" + k.replace("'", "''") + "'" for k in keys)
        try:
            det = {r["family_key"]: r for r in self.q(T.LEAD_AD_SQL.format(keys=lit))}
            lps: dict[str, dict] = {}
            if self.has("landing_page_ads", "landing_pages", "landing_page_versions"):
                for r in self.q(T.LP_FOR_ADS_SQL.format(keys=lit)):
                    lps.setdefault(r["family_key"], r)
        except BudgetExceeded:
            self.budget_hit = True
            return
        for c in cands:
            k = c["entity"]["key"]
            d = det.get(k)
            if not d:
                continue
            lp = lps.get(k)
            c["ad_detail"] = {"first_spend_day": str(d["first_day"]) if d["first_day"] else None, "created": str(d["created"]) if d["created"] else None, "ads_in_family": d["ads"], "product": d["product"], "stage": d["stage"],
                              "ad_set": d["adset_name"], "campaign": d["campaign_name"], "hook": d["hook"]}
            if lp:
                c["ad_detail"]["landing_page"] = {"url": lp["url"], "last_read": str(lp["last_read"]) if lp["last_read"] else None, "copy_since": str(lp["copy_since"]) if lp["copy_since"] else None, "versions": lp["versions"],
                                                  "title": lp["title"], "note": "copy_since = when the current copy was first seen (the last change unless the page was changed back)"}
                cs = _asd(lp["copy_since"]) if lp["copy_since"] else None
                center = _asd(c["break_date"]) if c.get("break_date") else pbreak
                if cs and center and abs((cs - center).days) <= 3:
                    c["change_events_within_3_days"].append({"type": "landing_page_version_changed", "date": str(cs), "label": lp["url"], "url": lp["url"]})
                    c["leaf_type"] = c["leaf_type"] or "a"
            elif d["link_in_ad_copy"]:
                c["ad_detail"]["landing_page"] = {"url": d["link_in_ad_copy"], "source": "a link written in the ad copy (not the destination field)", "copy_since": None}
            else:
                c["ad_detail"]["landing_page"] = None
                c["ad_detail"]["landing_page_gap"] = "destination link not in the database for this ad (landing_page_ads / landing_pages are not exposed yet): open the ad in Ads Manager > Destination"
            if c["leaf_type"] and not any(f.get("entity", {}).get("key") == k and f["node"].startswith("conv@family") for f in self.findings):
                pass
            for f in self.findings:
                if f.get("node", "").endswith("@family") and f["entity"]["key"] == k:
                    f["ad_detail"] = c["ad_detail"]
                    if c["change_events_within_3_days"]:
                        f["change_events_within_3_days"] = c["change_events_within_3_days"]
                        f["leaf_type"] = f["leaf_type"] or c["leaf_type"]

    def audience_pattern(self, res):
        rows = res["values_table"]
        def sh(t, w):
            return t["spend_share_" + w] or 0
        hot = [t for t in rows if re.search(r"exist|engag", str(t["label"]).lower())]
        cur = sum(sh(t, "cur") for t in hot)
        cmp_ = sum(sh(t, "cmp") for t in hot)
        out = {"existing_or_engaged_spend_share_cur": cur, "existing_or_engaged_spend_share_cmp": cmp_, "shift_points": (cur - cmp_) * 100}
        if cur - cmp_ >= 0.05:
            out["flag"] = ("Spend moved toward existing / engaged audiences by %.0f points: Meta-reported ROAS can rise while Upstack new customers fall. Compare with the Upstack new-customer basis (nc_roas, nc_cpa nodes); never mix the two." % ((cur - cmp_) * 100))
        return out

    def ad_breakdown_shift(self, cand, pbreak) -> None:
        if not self.has("meta_breakdown_daily") or self.product:
            return
        k = cand["entity"]["key"].replace("'", "''")
        sql = self.fmt_sql(T.BD_AD_SQL.replace("{periods_cte}", T.PERIODS).replace("{fk}", k))
        try:
            rows = self.q(sql)
        except BudgetExceeded:
            self.budget_hit = True
            return
        tot: dict[tuple, float] = {}
        for r in rows:
            tot[(r["b"], bool(r["inf"]), r["p"])] = tot.get((r["b"], bool(r["inf"]), r["p"]), 0) + float(r["spend"] or 0)
        cell: dict[tuple, dict] = {}
        for r in rows:
            c = cell.setdefault((r["b"], r["v"]), {})
            c[(bool(r["inf"]), r["p"])] = float(r["spend"] or 0)
        out = []
        for (b, v), c in cell.items():
            def share(inf, w):
                t = tot.get((b, inf, w), 0)
                return c.get((inf, w), 0) / t if t else None
            fc, fp, rc, rp = share(True, "cur"), share(True, "cmp"), share(False, "cur"), share(False, "cmp")
            if fc is None or fp is None or c.get((True, "cur"), 0) < 100:
                continue
            ent = {"breakdown": b, "value": v, "ad_family_share_cur": fc, "ad_family_share_cmp": fp, "ad_family_shift_points": (fc - fp) * 100,
                   "rest_of_account_shift_points": None if rc is None or rp is None else (rc - rp) * 100}
            if abs(fc - fp) >= 0.20:
                rest_ok = ent["rest_of_account_shift_points"] is not None and abs(ent["rest_of_account_shift_points"]) < 10
                ent["label"] = "supported by control" if rest_ok else "consistent with, untested"
                ent["control"] = "like-for-like: the rest of the account's mix on this value moved " + ("less than 10 points" if rest_ok else "too, or could not be read")
                ent["sentence"] = f"Ad {cand['entity']['label']}: spend on {v} ({b.replace('_', ' ')}) moved from {fp * 100:.0f}% to {fc * 100:.0f}% of its spend"
                self.findings.append({"entity": {"group": "ad_breakdown", "key": f"{cand['entity']['key']}|{b}|{v}", "label": f"{cand['entity']['label']} / {b}={v}", "what": "Meta breakdown at ad level (meta_breakdown_daily)"},
                                      "node": "bd_ad_shift", "parent_node": "conversion", "leaf_type": "b", "label": ent["label"], "label_reason": ent["control"], "share_of_parent_change": None,
                                      "change_events_within_3_days": [], "controls": {"like_for_like": {"rest_of_account_shift_points": ent["rest_of_account_shift_points"]}}, "claim": ent["sentence"],
                                      "break_date": cand.get("break_date"), "_path": [], "meta_reported_only": True})
            out.append(ent)
        out.sort(key=lambda x: -abs(x["ad_family_shift_points"]))
        cand.setdefault("ad_detail", {})["breakdown_shift"] = {"basis": "Meta-reported spend share, ad level; NULL ad_id rows are not used", "values": out[:8]}

    # ------------------------------------------------------------ routing shift
    def walk_routing(self, node, base, depth, parent, path) -> dict:
        pairs_out, skipped = [], []
        if self.platform == "google":
            return {**base, "status": "skipped", "reasons": ["platform is google"]}
        first_day = (self.coverage.get("meta_ad_daily") or {}).get("first_day")
        if first_day and min(self.start, self.cstart) < first_day:
            return {**base, "status": "data_gap", "data_gap": {"why": f"ad-level data starts {first_day}; a window starts earlier", "where": "analytics.meta_campaign_daily_history"}}
        jobs = []
        for d1, d2 in T.ROUTING_PAIRS:
            spec, g1 = T.D2[d2], T.MIX_GROUPS[d1]
            if self.product and d1 == "product":
                continue
            if not self.has(*spec[2], *g1["needs"]):
                skipped.append({"pair": f"{d1} x {spec[3]}", "reason": "landing_page_ads / landing_pages are not exposed in analytics yet"})
                self.gaps.append({"node": node["id"], "why": f"{d1} x {spec[3]}: landing_page_ads not exposed yet", "where": "feat/lp-copy-store (PR #95)"})
                continue
            sql = (T.ROUTING_SQL.replace("{periods_cte}", T.PERIODS).replace("{d1}", g1["key"]).replace("{d2}", spec[0]).replace("{extra}", g1["extra"] + spec[1]))
            jobs.append((f"{d1} x {spec[3]}", d1, d2, sql, "upstack new customers (attributed, any-click)"))
        for b, d2 in T.ROUTING_BD_PAIRS:
            if self.has("meta_breakdown_daily") and not self.product:
                jobs.append((f"{b.replace('_', ' ')} x {d2}", "bd_" + b, d2, T.ROUTING_BD_SQL.replace("{periods_cte}", T.PERIODS).replace("{b}", b), "Meta-reported purchases"))
            elif not self.product:
                skipped.append({"pair": f"{b.replace('_', ' ')} x {d2}", "reason": "Meta breakdowns are not stored yet (analytics.meta_breakdown_daily)"})
        for b, what in T.ROUTING_BD_GAPS:
            skipped.append({"pair": f"{b.replace('_', ' ')} x {what}", "reason": "the breakdown view has no ad id link to a page or family for segment rows pulled above ad level; ad-level segment shifts are read in the family drill-down instead"})
        findings_here = []
        cp = [p for p in self.periods if p[0] in ("cur", "cmp")]
        for name, d1, d2, sql, basis in jobs:
            try:
                rows = self.q(self.fmt_sql(sql, cp))
            except BudgetExceeded:
                self.budget_hit = True
                break
            found = self.routing_pair(name, d1, d2, rows, basis, path, parent)
            pairs_out.append({"pair": name, "basis": basis, "flagged": len(found)})
            findings_here += found
        status = "material" if any(f["label"] != "refuted" for f in findings_here) else "flat"
        self.routing_cov = {"pairs_checked": pairs_out, "skipped_pairs": skipped}
        return {**base, "status": status, "pairs_checked": pairs_out, "skipped_pairs": skipped, "routing_findings": [{k: v for k, v in f.items() if not k.startswith("_")} for f in findings_here],
                "known_causes": self.kc_block(node, {"status": status, "values": {"rel": -1 if status == "material" else 0}}, None), "children": [], "not_followed": [], "_path": path}

    def routing_pair(self, name, d1, d2, rows, basis, path, parent) -> list[dict]:
        cell: dict[tuple, dict] = {}
        dims = set()
        for r in rows:
            dims.add(r["dk"])
            cell[(r["dk"], r["pg"], r["p"])] = {x: float(r[x] or 0) for x in ("spend", "clicks", "nc", "nc_rev")}

        def conv(c):
            return c["nc"] / c["clicks"] if c and c["clicks"] else None
        out = []
        tot_clicks_cur = sum(c["clicks"] for (dk, pg, p), c in cell.items() if p == "cur")
        tot_conv = {w: (sum(c["nc"] for (dk, pg, p), c in cell.items() if p == w) / max(1e-9, sum(c["clicks"] for (dk, pg, p), c in cell.items() if p == w))) for w in ("cur", "cmp")}
        gap_total = tot_clicks_cur * (tot_conv["cmp"] - tot_conv["cur"])
        for dk in sorted(dims):
            dest = {w: {pg: c["spend"] for (k, pg, p), c in cell.items() if k == dk and p == w} for w in ("cur", "cmp")}
            tc, tp = sum(dest["cur"].values()), sum(dest["cmp"].values())
            if tc < 300 or tp < 300:
                continue
            sc = {pg: v / tc for pg, v in dest["cur"].items()}
            sp = {pg: v / tp for pg, v in dest["cmp"].items()}
            allp = set(sc) | set(sp)
            tvd = 0.5 * sum(abs(sc.get(pg, 0) - sp.get(pg, 0)) for pg in allp)
            if tvd < 0.30:
                continue
            real = [pg for pg in allp if pg != "~other"]
            if len(real) < 2:
                continue
            new = max(real, key=lambda pg: sc.get(pg, 0) - sp.get(pg, 0))
            old = min(real, key=lambda pg: sc.get(pg, 0) - sp.get(pg, 0))
            c_new, c_old_cmp, c_old_cur = cell.get((dk, new, "cur")), cell.get((dk, old, "cmp")), cell.get((dk, old, "cur"))
            cv_new, cv_old, cv_old_cur = conv(c_new), conv(c_old_cmp), conv(c_old_cur)
            if not cv_new or not cv_old or c_new["clicks"] < 300 or c_old_cmp["clicks"] < 300:
                continue
            roas = lambda c: (c["nc_rev"] / c["spend"]) if c and c["spend"] else None  # noqa: E731
            worse_conv = cv_new / cv_old - 1
            rn, ro = roas(c_new), roas(c_old_cmp)
            worse_roas = (rn / ro - 1) if rn is not None and ro else None
            if worse_conv > -0.25 and (worse_roas is None or worse_roas > -0.25):
                continue
            old_stable = None if not cv_old_cur or (c_old_cur["clicks"] < 100) else abs(cv_old_cur / cv_old - 1) <= 0.15
            others_new = [(c["nc"], c["clicks"]) for (k, pg, p), c in cell.items() if k != dk and pg == new and p == "cur"]
            others_else = [(c["nc"], c["clicks"]) for (k, pg, p), c in cell.items() if k != dk and pg != new and p == "cur"]
            on, oc = sum(x[0] for x in others_new), sum(x[1] for x in others_new)
            en, ec = sum(x[0] for x in others_else), sum(x[1] for x in others_else)
            others_worse = None
            if oc >= 300 and ec >= 300 and en:
                others_worse = (on / oc) / (en / ec) - 1 <= -0.25
            within = (cv_new / cv_old_cur - 1) if cv_old_cur and c_old_cur["clicks"] >= 100 else None
            if within is not None and within > -0.10:
                label, kind, why = "refuted", "routing_shift", "within the same window the same value converts no worse on the new destination than on the old one"
            elif others_worse is True:
                label, kind, why = "supported by control", "page_problem", "the new destination is also worse for the other values: a destination problem, not a routing shift"
            elif others_worse is False and old_stable is not False:
                label, kind, why = "supported by control", "routing_shift", "the old destination still converts as before on its remaining spend, and the new one is not worse for the other values"
            else:
                label, kind, why = "consistent with, untested", "routing_shift", "a control could not be completed (" + ("the old destination also changed" if old_stable is False else "too little traffic from other values on the new destination") + ")"
            cur_all = {k: v for (dk2, pg, p), v in cell.items() if dk2 == dk and p == "cur" for k in [pg]}
            ncs = {w: sum(c["nc"] for (k, pg, p), c in cell.items() if k == dk and p == w) for w in ("cur", "cmp")}
            cls = {w: sum(c["clicks"] for (k, pg, p), c in cell.items() if k == dk and p == w) for w in ("cur", "cmp")}
            cvdk = {w: ncs[w] / cls[w] if cls[w] else None for w in ("cur", "cmp")}
            revs = sum(c["nc_rev"] for (k, pg, p), c in cell.items() if k == dk and p == "cur")
            aov = revs / ncs["cur"] if ncs["cur"] else None
            est_nc = cls["cur"] * (cvdk["cmp"] - cvdk["cur"]) if cvdk["cmp"] is not None and cvdk["cur"] is not None else None
            est_rev = est_nc * aov if est_nc is not None and aov else None
            first = None
            if d2 != "page" or True:
                try:
                    g1 = T.MIX_GROUPS[d1] if not d1.startswith("bd_") else None
                    spec2 = T.D2.get(d2)
                    if g1 and spec2 and not (d2 == "page" and not self.has("landing_page_ads")):
                        sqlf = T.ROUTING_FIRST_USE_SQL.replace("{extra}", g1["extra"] + spec2[1]).replace("{d1}", g1["key"]).replace("{d2}", spec2[0]).replace("{v1}", str(dk).replace("'", "''")).replace("{v2}", str(new).replace("'", "''"))
                        fr = self.q(self.fmt_sql(sqlf))
                        if fr:
                            first = {"first_day_on_new_destination": str(min(_asd(x["first_day_on_dest"]) for x in fr)), "families": [{"family": x["fname"], "family_first_day": str(x["family_first_day"]), "spend": float(x["spend"])} for x in fr[:3]]}
                except BudgetExceeded:
                    self.budget_hit = True
            share = (est_nc / gap_total) if est_nc is not None and gap_total else None
            sentence = (f"{name.split(' x ')[0].capitalize()} {dk} moved from {old} ({sp.get(old, 0) * 100:.0f}% of its spend, conversion {cv_old * 100:.2f}%) to {new} ({sc.get(new, 0) * 100:.0f}% now, conversion {cv_new * 100:.2f}%)"
                        + (f" starting {first['first_day_on_new_destination']}" if first else "") +
                        (f", when ad family {first['families'][0]['family']} launched with it as its destination" if first and first["families"] and first["families"][0]["family_first_day"] >= first["first_day_on_new_destination"][:8] and abs((_asd(first["families"][0]["family_first_day"]) - _asd(first["first_day_on_new_destination"])).days) <= 3 else "") +
                        (f". Same-value control: {old} converts {cv_old_cur * 100:.2f}% on its remaining spend" if cv_old_cur else "") + f" -> {label}")
            f = {"entity": {"group": d1, "key": dk, "label": dk, "what": name}, "node": "routing_shift", "parent_node": "conversion", "leaf_type": "a" if first else "b", "label": label, "label_reason": why,
                 "kind": kind, "share_of_parent_change": share, "break_date": first["first_day_on_new_destination"] if first else None, "break_basis": "first day this value had spend on the new destination" if first else None,
                 "routing": {"pair": name, "basis": basis, "old_destination": old, "new_destination": new, "distribution_shift_points": tvd * 100, "old_share_before": sp.get(old, 0), "old_share_now": sc.get(old, 0),
                             "new_share_before": sp.get(new, 0), "new_share_now": sc.get(new, 0), "conversion_old_before": cv_old, "conversion_new_now": cv_new, "conversion_old_now": cv_old_cur,
                             "roas_old_before": ro, "roas_new_now": rn, "new_vs_old_conversion": worse_conv, "within_window_new_vs_old": within, "others_on_new_worse": others_worse},
                 "first_use": first, "controls": {"old_destination_still_converts": old_stable, "others_on_new_destination_worse": others_worse, "within_window_new_vs_old": within},
                 "counterfactual_estimate": {"label": "estimate (clicks and AOV held at today's level, conversion at the old routing's level)", "extra_new_customers": est_nc, "extra_new_customer_revenue": est_rev},
                 "claim": sentence, "change_events_within_3_days": [], "_path": path + [{"node": "routing_shift", "label": "Routing shift", "rel": None}]}
            out.append(f)
            self.findings.append(f)
        return out

    # ------------------------------------------------------------ assembling the answer
    def sentence(self, f) -> str:
        if f.get("claim"):
            return f["claim"]
        e = f["entity"]
        r = f.get("rate")
        if r == "aov":
            name = e["group"]
            lab = "single-bottle" if str(e["label"]).startswith("1 bottle") else e["label"]
            up = (f.get("share_cur") or 0) > (f.get("share_cmp") or 0)
            lfl = (f.get("controls") or {}).get("like_for_like", {}).get("share_of_gap")
            s = (f"{'pack mix' if name == 'pack' else name.replace('_', ' ') + ' mix'}: {lab} share {'up' if up else 'down'} from {(f.get('share_cmp') or 0) * 100:.0f}% to {(f.get('share_cur') or 0) * 100:.0f}% of new-customer orders"
                 f" (AOV {f.get('rate_cur', 0):,.2f} vs {f.get('rate_cmp', 0):,.2f} before"
                 + (f"; AOV within the other values explains {abs(lfl) * 100:.0f}% of the change" if lfl is not None else "") + ")")
            ev = (f.get("change_events_within_3_days") or [None])[0]
            if ev:
                s += f"; dated change within 3 days of the break: {ev['type'].replace('_', ' ')} '{ev.get('label')}' on {ev['date']}" + (f" (page {ev['landing_page']['url']})" if ev.get("landing_page") else "")
            return s
        fm = {"roas": lambda v: f"{v:.2f}" if v is not None else "n/a", "conv": lambda v: f"{v * 100:.2f}%" if v is not None else "n/a", "cpm": lambda v: f"${v:.2f}" if v is not None else "n/a",
              "meta_roas": lambda v: f"{v:.2f}" if v is not None else "n/a", "meta_conv": lambda v: f"{v * 100:.2f}%" if v is not None else "n/a"}.get(r, lambda v: str(v))
        what = {"roas": "new-customer ROAS (Upstack attributed)", "conv": "attributed conversion (new customers per link click)", "cpm": "CPM", "meta_roas": "Meta-reported ROAS", "meta_conv": "Meta-reported conversion"}.get(r, "rate")
        sh = f.get("spend_share_cur")
        s = f"{e['group']} '{e['label']}'" + (f" took {sh * 100:.0f}% of spend" if sh is not None and r != "cpm" else "") + f" at {what} {fm(f.get('rate_cur'))} vs {fm(f.get('rate_rest_cur'))} for the rest (before: {fm(f.get('rate_cmp'))})"
        d = f.get("ad_detail") or {}
        lp = d.get("landing_page")
        if lp and lp.get("url"):
            s += f"; its link is {lp['url']}" + (f" (page copy last changed {lp['copy_since']})" if lp.get("copy_since") else "")
        elif d.get("landing_page_gap"):
            s += f"; link: {d['landing_page_gap']}"
        if f.get("change_events_within_3_days"):
            ev = f["change_events_within_3_days"][0]
            s += f"; dated change within 3 days of the break: {ev['type'].replace('_', ' ')} on {ev['date']}"
        return s

    def action(self, f) -> str:
        e, lab = f["entity"], f["entity"].get("label")
        if f.get("kind") == "page_problem":
            return f"Fix or pull {f['routing']['new_destination']}: it converts worse for every {e['group']}, so this is a page problem, not a routing change."
        if f["node"] == "routing_shift":
            return f"Route {e['group']} {lab} back to {f['routing']['old_destination']} (or fix {f['routing']['new_destination']} for it) and re-read conversion over 3+ days."
        if f["node"] == "bd_ad_shift":
            return f"Exclude or cap the audience segment that took the spend for {e['label']}, then compare Upstack new customers (not Meta purchases)."
        if e["group"] == "family":
            return f"Cap, pause or hold out ad family '{lab}' for 3+ days and re-read account conversion; check its destination page first."
        if e["group"] in ("g_type", "g_campaign"):
            return f"Review Google {lab}: spend share and Upstack-attributed CPA moved."
        if e["group"] == "landing_page":
            return f"Check page {lab}: conversion for the ads that send to it moved."
        return f"Review spend allocation toward / away from {e['group']} '{lab}'; re-run why after 3+ days."

    def settle(self, f) -> str:
        e = f["entity"]
        if f["node"] == "routing_shift":
            return "route a slice of the same value back to the old destination for 3+ days and compare conversion on the same days"
        if f["node"] == "bd_ad_shift":
            return "exclude the segment on this ad and compare Upstack new-customer conversion, not Meta purchases"
        if e["group"] == "family":
            return "pause or hold out the family for 3+ days and compare account conversion with the same weekdays; or compare its conversion with sibling ads in the same ad set on the same days"
        if e["group"] == "cpm" or f.get("leaf_type") == "c":
            return "an Ads Manager breakdown by country and age for the same days (not in the database)"
        return "a holdout or a like-for-like cell that did not get the change"

    def finalize(self, tree) -> dict:
        for f in self.findings:
            sh = 1.0
            chain = []
            for en in f.get("_path", []):
                if en.get("share_of_parent") is not None:
                    sh *= en["share_of_parent"]
                chain.append({k: v for k, v in en.items() if k in ("node", "label", "rel", "cur", "cmp", "unit", "denominator", "share_of_parent")})
            ef = f.get("share_of_parent_change")
            f["share_of_root_change_estimate"] = None if ef is None else sh * ef
            f["cause_chain"] = chain + [{"entity": f["entity"], "leaf_type": {"a": "dated change event", "b": "entity carrying >= 50% of the parent's change", "c": "market-wide pattern"}.get(f.get("leaf_type"), f.get("kind") or "entity")}]
            f["sentence"] = self.sentence(f)
            f["suggested_action"] = self.action(f)
            f["settle_with"] = self.settle(f)
            nid = f["node"]
            pq = T.BY_ID.get(nid, {}).get("pq") or T.BY_ID.get(f.get("parent_node"), {}).get("pq")
            f["past_questions"] = self.past_questions({"pq": pq})
        order = {"supported by control": 0, "consistent with, untested": 1, "refuted": 2}
        spec = {"bd_ad_shift": 0, "routing_shift": 1, "family": 1, "landing_page": 2, "cluster": 3, "icp": 3, "pain_point": 3, "emotional_trigger": 3, "ad_type": 3, "style": 3, "vendor": 3, "product": 4, "stage": 4}
        ranked = sorted(self.findings, key=lambda f: (order.get(f["label"], 1), -round(abs(f.get("share_of_root_change_estimate") or f.get("share_of_parent_change") or 0) / 0.1), spec.get(f["node"] if f["node"] in spec else f["entity"]["group"], 5),
                                                      -abs(f.get("share_of_root_change_estimate") or f.get("share_of_parent_change") or 0)))
        seen_r, dedup = set(), []
        for f in ranked:
            kk = (f["entity"]["group"], f["entity"]["key"], f["node"] == "routing_shift")
            if f["label"] == "refuted" and kk in seen_r:
                continue
            seen_r.add(kk)
            dedup.append(f)
        ranked = dedup
        live = [f for f in ranked if f["label"] != "refuted"]
        refuted = [f for f in ranked if f["label"] == "refuted"]
        supported = [f for f in live if f["label"] == "supported by control"]
        explained = max([abs(f.get("share_of_root_change_estimate") or 0) for f in supported] or [0.0])
        explained = min(1.0, explained)
        for f in live:
            self.already_done(f)
        matches = []

        def collect(n):
            for k in n.get("known_causes", []) or []:
                if k.get("matches_current_pattern"):
                    matches.append({"node": n["id"], "date": k["date"], "claim": k["claim"], "status": k["status"], "confidence": k["confidence"], "between": k["between"], "note": k.get("note")})
            for c in n.get("children", []):
                collect(c)
        collect(tree)
        self.leaf_nodes: list[dict] = []

        def leaves(n):
            kids = [c for c in n.get("children", []) if c.get("status") == "material" and c["kind"] == "scalar"]
            if n.get("status") == "material" and n["kind"] == "scalar" and not kids and not any(c.get("status") == "material" for c in n.get("children", [])):
                self.leaf_nodes.append(n)
            for c in n.get("children", []):
                leaves(c)
        leaves(tree)
        nq = []
        if not supported:
            for n in self.leaf_nodes[:4]:
                nq.append({"question": f"What settles '{n['label']}' ({pct((n.get('values') or {}).get('rel'))})?", "where_or_test": n.get("settles_with") or "the node's children"})
        for f in live:
            if f["label"] == "consistent with, untested":
                nq.append({"question": f"Is {f['entity']['group']} '{f['entity'].get('label')}' the cause?", "where_or_test": f["settle_with"]})
        seen = set()
        for g in self.gaps:
            key = (g.get("where"), g.get("why"))
            if key in seen:
                continue
            seen.add(key)
            nq.append({"question": g.get("why"), "where_or_test": g.get("where")})
        if self.budget_hit:
            nq.append({"question": "The query budget (max_steps) ran out before every branch was read.", "where_or_test": "re-run with a larger max_steps, or start from the node that matters (metric=...)"})
        unexplained = None
        if not supported:
            unexplained = {"share_of_change": 1.0, "text": "unexplained: 100% of the change (no cause survived a control)" if not live else
                           f"unexplained: {100 - round(explained * 100)}% of the change; the candidates are consistent with, not supported by, a control", "next_questions": nq}
        elif explained < 0.8:
            unexplained = {"share_of_change": 1 - explained, "text": f"unexplained: about {round((1 - explained) * 100)}% of the change (estimate; shares are multiplied down the chain)", "next_questions": nq}
        chain_text = self.chain_text(tree, live[0] if live else None)
        reading = []
        gb = (tree.get("grains") or {})
        if gb.get("when_it_broke"):
            wb = gb["when_it_broke"]
            reading.append("When it broke (" + tree["label"] + "): " + "; ".join(f"{g}: {v['shape']}" + (f", first out of baseline {v['break']} ({v['direction']})" if v.get("break") else "") for g, v in wb.items()) + ".")
        if gb.get("disagreement"):
            reading.append(gb["disagreement"] + ".")
        for f in live[:3]:
            word = "Supported by control" if f["label"] == "supported by control" else "Consistent with, untested"
            reading.append(f"{word}: {f['sentence']}. Control: {f['label_reason']}. Settle with: {f['settle_with']}.")
        for f in refuted[:3]:
            reading.append(f"Refuted: {f['entity']['group']} '{f['entity'].get('label')}' ({f['label_reason']}).")
        for k in matches[:3]:
            reading.append((f"Previously refuted, do not re-claim ({k['date']}): {k['claim']}." if k["status"] == "refuted" else f"Matches a known pattern ({k['status']}, {k['date']}): {k['claim']}."))
        if unexplained:
            reading.append(unexplained["text"] + ".")
        changes = self.changes_block()
        grd = self.guardrails(tree, live)
        out = {"guardrails_checked": grd, "metric": self.metric, "window": {"start": str(self.start), "end": str(self.end)}, "comparison": {"kind": self.compare, "start": str(self.cstart), "end": str(self.cend)},
               "scope": {"product": self.product, "platform": self.platform, "basis": ("Upstack-attributed (any-click) new customers from the ad views, not actual orders" if self.scoped else "actual orders (orders_daily) and platform spend; gross, tax in, refunds not out")},
               "headline": chain_text, "arrows_mean": "'of which' (accounting identity), not 'because'", "reading": reading, "findings": [self._clean(f) for f in live], "refuted_candidates": [self._clean(f) for f in refuted],
               "unexplained": unexplained, "next_questions": nq, "changes_in_window": changes, "known_cause_matches": matches, "tree": self._clean(tree), "steps_used": self.steps, "max_steps": self.max_steps, "budget_exhausted": self.budget_hit,
               "routing_shift_coverage": getattr(self, "routing_cov", None), "coverage_notes": self.notes, "queries_run": self.steps}
        return out

    def chain_text(self, tree, finding) -> str:
        def short(n):
            return re.sub(r"\s*\(.*?\)", "", n["label"]).strip()
        def ph(n):
            return f"{short(n)} {pct((n.get('values') or {}).get('rel'))}"
        if tree.get("status") != "material":
            return f"{short(tree)} {pct((tree.get('values') or {}).get('rel'))}: flat or below the materiality threshold ({'; '.join(tree.get('reasons') or [])}); the cascade stops here."
        segs = [f"{short(tree)} {pct(tree['values']['rel'])} ({fmt(tree['values']['cur'], tree['unit'])} vs {fmt(tree['values']['cmp'], tree['unit'])}{' per day' if not T.BY_ID[tree['id']].get('den') and tree['unit'] == 'usd' else ''})"]
        path_ids = {e["node"] for e in (finding or {}).get("_path", [])} if finding else set()
        node = tree
        while True:
            kids = [c for c in node.get("children", []) if c["kind"] == "scalar" and c.get("status") == "material"]
            if not kids:
                break
            others = [n for n in node.get("not_followed", []) if n.get("rel") is not None and T.BY_ID[n["id"]]["kind"] == "scalar" and any(c["id"] == n["id"] and c["role"] == "part" for c in T.BY_ID[node["id"]]["children"])]
            seg = ", ".join(ph(c) for c in kids)
            if node.get("roas_split"):
                seg = node["roas_split"]["chain"]
            if others:
                seg += " (" + ", ".join(f"{short(T.BY_ID[o['id']])} {pct(o['rel'])}" for o in others) + ")"
            segs.append(seg)
            nxt = [c for c in kids if c["id"] in path_ids] or sorted(kids, key=lambda c: -abs(c.get("share_of_parent_change") or 0))[:1]
            node = nxt[0]
        if finding:
            segs.append(finding["sentence"] + f" [{finding['label']}]")
        elif node.get("status") == "material":
            segs.append("no concrete cause found below this node")
        return " -> ".join(segs)

    ACT_VERBS = re.compile(r"\b(paus|turn(ed)? off|turned it off|cap(ped)?\b|relaunch|route|exclude|loosen|tighten|stop(ped)?\b|kill|swap|replace|fix(ed)?\b|raised?\b|lowered?\b)", re.I)

    def already_done(self, f) -> None:
        """G22: before suggesting an action, look for a decision item that already did it (full entity name, an action verb, within 60 days before the window ends)."""
        names = [str(f["entity"].get("label") or ""), str((f.get("routing") or {}).get("new_destination") or ""), str((f.get("routing") or {}).get("old_destination") or "")]
        keys = {re.sub(r"[^a-z0-9]+", " ", n.lower()).strip() for n in names if len(n) >= 6}
        keys.discard("")
        if not keys or not self.kn:
            f["already_done"] = []
            return
        lo = self.end - dt.timedelta(days=60)
        hits = []
        for it in self.kn.questions:
            if it.get("kind") != "decision_made" or not it.get("date"):
                continue
            try:
                d = dt.date.fromisoformat(str(it["date"])[:10])
            except ValueError:
                continue
            text = re.sub(r"[^a-z0-9]+", " ", (str(it.get("verbatim") or "") + " " + str(it.get("intent") or "")).lower())
            if lo <= d <= self.end and self.ACT_VERBS.search(text) and any(k in text for k in keys):
                hits.append({"id": it.get("id"), "date": str(it["date"])[:10], "verbatim": str(it.get("verbatim") or "")[:200]})
        hits.sort(key=lambda h: h["date"], reverse=True)
        f["already_done"] = hits[:2]
        if hits:
            f["suggested_action"] = f"Already acted on ({hits[0]['date']}, {hits[0]['id']}): do not repeat it; check whether it worked by re-running why after 3+ days."

    def guardrails(self, tree, live) -> list[dict]:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()
        rel = ("orders", "platform", "meta_ad_daily", "meta_reach", "google", "klaviyo", "shopify", "landing", "meta_breakdown")
        stale = [f"{k} loaded through {r['last_day']}" for k, r in self.coverage.items() if r.get("last_day") and r["last_day"] < self.end and k.startswith(rel)]
        loaded = [r["last_day"] for k, r in self.coverage.items() if r.get("last_day") and k.startswith(("orders", "platform", "meta_ad_daily"))]
        partial = self.end >= today
        scalars = []

        def walk(n):
            if n.get("kind") == "scalar" and n.get("status") in ("material", "flat"):
                scalars.append(n)
            for c in n.get("children", []):
                walk(c)
        walk(tree)
        den_ok = all(n.get("denominator") for n in scalars)
        mix_f = [f for f in live if f.get("mix_vs_rate") is not None or f["node"] in ("routing_shift", "bd_ad_shift") or f.get("leaf_type") == "c"]
        sup_nocontrol = [f for f in live if f["label"] == "supported by control" and not f.get("controls")]
        return [
            {"id": "G01", "rule": "name the basis before comparing", "ok": True, "result": self.scope_basis()},
            {"id": "G03", "rule": "no partial day, unloaded day or partial window", "ok": not stale and not partial, "result": (f"window ends {self.end}; last loaded day {min(loaded) if loaded else 'unknown'}"
                      + ("; the window includes today (a partial day)" if partial else "") + ("; " + "; ".join(stale) if stale else "") + ". The current month is only compared with the same days of earlier months.")},
            {"id": "G04", "rule": "never read a month that is not over as a full month", "ok": True, "result": "month grain: closed months are shown whole; the open month is month-to-date against the same days of the prior months"},
            {"id": "G05", "rule": "gross vs net", "ok": True, "result": "all revenue is gross: tax included, before refunds (orders_daily, Shopify total_price); compare with the P&L only after netting"},
            {"id": "G06", "rule": "Pacific days", "ok": True, "result": "days are Pacific for orders, Google and Upstack; Meta spend uses each ad account's own day (the BackUp account is New York time); weeks start Monday"},
            {"id": "G07", "rule": "rates carry denominators, never bare counts", "ok": den_ok, "result": f"{len(scalars)} nodes read, each with its denominator (per ... in `denominator`); amounts are per day over the window"},
            {"id": "G08", "rule": "mix vs rate before naming a cause", "ok": all(f.get("mix_vs_rate") is not None or f["node"] in ("routing_shift", "bd_ad_shift") or f.get("leaf_type") == "c" for f in live), "result": f"{len(mix_f)} of {len(live)} findings carry the mix / rate split (accounting) or a routing / breakdown control"},
            {"id": "G10", "rule": "timing or decomposition is not attribution", "ok": not sup_nocontrol, "result": "'supported by control' is only given after a counterfactual exclusion; dated events within 3 days are candidates and are labelled by their control, never by timing alone"},
            {"id": "G15", "rule": "account scope in ROAS", "ok": True, "result": "NC ROAS = new-customer revenue (orders_daily, all sources) / spend of Meta (all ad accounts, via platform_daily) + Google over the same days; Meta-only ratios are shown as Meta-reported, separate columns"},
            {"id": "G19", "rule": "results are not causes", "ok": True, "result": "single-cut ratios (audience, placement, age mix) are shown as accounting; only entities that survive a control are labelled supported"},
            {"id": "G22", "rule": "do not re-recommend what was already done", "ok": True, "result": f"{sum(1 for f in live if f.get('already_done'))} of {len(live)} suggested actions matched a decision item from the 60 days before the window; those say 'already acted on'"},
        ]

    def scope_basis(self) -> str:
        if self.scoped:
            return "scoped to a product / platform: Upstack-attributed (any-click) new customers from the ad views, not actual orders; Meta-reported shown separately"
        return "actual orders (orders_daily, all sources) with Meta + Google spend; Meta-reported and Upstack-attributed numbers appear only as separate labelled columns"

    def changes_block(self) -> dict:
        try:
            ev = self.events()
        except BudgetExceeded:
            ev = []
        lo = self.start - dt.timedelta(days=3)
        by: dict[str, list] = {}
        for e in ev:
            if e["date"] is None or lo <= e["date"] <= self.end:
                by.setdefault(e["type"], []).append({k: (str(v) if isinstance(v, dt.date) else v) for k, v in e.items() if k not in ("type",)})
        return {"window_for_dated_events": [str(lo), str(self.end)], "counts": {k: len(v) for k, v in by.items()}, "examples": {k: v[:6] for k, v in by.items()},
                "klaviyo_flows": "Klaviyo not in the database yet" if not self.has("klaviyo_flows") else "read from analytics.klaviyo_flows (created / updated)",
                "landing_page_versions": "landing pages not in the database yet" if not self.has("landing_page_versions") else "read from analytics.landing_page_versions",
                "not_in_the_database": T.NOT_IN_DB}

    def _clean(self, o):
        if isinstance(o, dict):
            return {k: self._clean(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, (list, tuple)):
            return [self._clean(v) for v in o]
        if isinstance(o, (dt.date, dt.datetime)):
            return str(o)
        if isinstance(o, float):
            return None if math.isnan(o) else (None if math.isinf(o) else round(o, 6))
        return o


def run_why(args: dict, run: Callable[[str, int], dict], kn=None) -> dict:
    w = Why(run, kn, max_steps=int(args.get("max_steps") or 40))
    w.setup(args)
    tree = w.walk(w.metric, 1, None, [])
    return w.finalize(tree)
