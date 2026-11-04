"""Ported from ~/lonvera-upstack-sync/meta_history.py + meta_campaign_history.py (do not import from that repo).
Pure helpers: fam / norm / triple / Resolver / pick / pick_clicks / classify_stage / classify_product."""
from __future__ import annotations

import re


MERGE_SUFFIX = re.compile(r"\s*\|\s*[A-Za-z0-9]+-merge-\d{4}-\d{2}-\d{2}\s*$", re.I)
COPY_SUFFIX = re.compile(r"(\s*-\s*Copy(\s*\d+)?)+\s*$", re.I)


def fam(n) -> str:
    """Meta ad name -> ad family: strip trailing ' - Copy' / ' - Copy 2' and a merge tag ' | <word>-merge-<date>' (e.g. ' | vb-merge-2026-10-22'), in any order."""
    s = str(n)
    while True:
        t = COPY_SUFFIX.sub("", MERGE_SUFFIX.sub("", s)).strip()
        if t == s:
            return t
        s = t


def norm(x) -> str:
    return re.sub(r"[_\-\s]+", "-", str(x).lower()).strip("-")


PROD = {"beefliver": "liver", "liver": "liver", "vitalbull": "bull", "vitabull": "bull", "bull": "bull",
        "boneflex": "bone", "vitalbelle": "belle", "vitabelle": "belle", "belle": "belle", "vitalflow": "flow", "flow": "flow"}


def triple(name):
    toks = [t for t in re.split(r"[_\-\s/|]+", str(name).lower()) if t]
    p = next((PROD[t] for t in toks if t in PROD), None)
    ty = next(("static" if t.startswith("static") else "video" for t in toks if t in ("static", "statics", "video", "vid")), None)
    v = next((t for t in toks if re.fullmatch(r"\d+\.\d+", t)), None)
    return (p, ty, v) if p and ty and v else None


class Resolver:
    """Meta ad family -> Creative Tracker Ad Family. Rows: dicts with 'family', 'concept', 'adname'.
    resolve() -> tracker family (literal case) or None; resolve_rule() -> (family|None, rule)."""
    CUTOFF = "2026-06-20"

    def __init__(self, rows, created=None):
        self.created = created if created is not None else {}
        self.fam_by = {}
        self.gen = {}
        self.ctrip = {}
        for r in rows:
            f = fam(r.get("family", ""))
            if not f:
                continue
            self.fam_by.setdefault(norm(f), f)
            if str(r.get("adname", "")).strip():
                self.gen.setdefault(norm(r["adname"]), f)
            c = str(r.get("concept", "")).strip()
            if re.match(r"(?i)^lonvera[-_ ]", c):
                t = triple(fam(c))
                if t:
                    self.ctrip.setdefault(t, f)
        self.pre = sorted([(k, v) for k, v in self.fam_by.items() if k.startswith("lonvera")], key=lambda x: -len(x[0]))
        self.cache = {}

    def resolve(self, meta_family):
        return self.resolve_rule(meta_family)[0]

    def resolve_rule(self, meta_family):
        if meta_family not in self.cache:
            self.cache[meta_family] = self._resolve(str(meta_family))
        return self.cache[meta_family]

    def _resolve(self, name):
        k = norm(fam(name))
        if k in self.fam_by:                                   # 1 exact
            return self.fam_by[k], "exact"
        m = re.search(r"_(VID|IMG)_", name)                    # 2 relaunch-encoded
        if m:
            b = norm(fam(name[:m.start()]))
            if b in self.fam_by:
                return self.fam_by[b], "relaunch"
        if norm(name) in self.gen:                             # ad-name match (tracker 'Name')
            return self.gen[norm(name)], "adname"
        for c, lit in self.pre:                                # 3 concept prefix
            if k.startswith(c + "-"):
                return lit, "prefix"
        t = triple(name)                                       # 4 product + type + version
        if t in self.ctrip and (self.created.get(fam(name).lower()) or "0000") >= self.CUTOFF:
            return self.ctrip[t], "triple"
        return None, None


def pick(actions):
    d = {x["action_type"]: float(x["value"]) for x in (actions or [])}
    return d.get("omni_purchase", d.get("purchase", d.get("offsite_conversion.fb_pixel_purchase", 0.0)))


def pick_clicks(a):
    d = {x["action_type"]: float(x["value"]) for x in (a.get("actions") or [])}
    if "link_click" in d:
        return d["link_click"]
    return float(a.get("inline_link_clicks") or 0)


def classify_stage(name) -> str:
    """Testing if the name contains 'test' (case-insensitive), else Scaling (meta_campaign_history.classify_stage)."""
    return "testing" if "test" in str(name).lower() else "scaling"


def classify_product(name) -> str:
    n = str(name).lower()
    hits = set()
    if re.search(r"vita ?bull|vital ?bull|^bull", n):
        hits.add("VitaBull")
    if re.search(r"beef ?liver|^liver", n):
        hits.add("Beef Liver")
    if re.search(r"bone ?flex|^bone\b", n):
        hits.add("Boneflex")
    return next(iter(hits)) if len(hits) == 1 else "Unclassified"
