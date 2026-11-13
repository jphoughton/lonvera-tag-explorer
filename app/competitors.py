"""Competitors (/competitors, /api/competitors): a weekly, read-only view of what watched competitors run on Meta, built from the tables written by etl.competitor_ads (migration 195).

Nothing here calls Meta, Google or Reddit and nothing is written. Week-over-week facts compare the selected snapshot with the stored week just before it; with one stored week they are None / empty and the
payload says so. ?format=video|image limits every number to that format (the Ad Library only tells video from image: carousels read as image). See docs/COMPETITOR_ADS.md.
"""
from __future__ import annotations

import collections
import datetime as dt
import re
from urllib.parse import quote, urlparse

from . import competitors_learn as learn
from . import queries as q

LIBRARY = "https://www.facebook.com/ads/library/"
FORMATS = ("video", "image")
TOP_ADS = 8
GALLERY_MAX = 12                   # curated ads on a brand's detail: its longest-running plus up to GALLERY_NEW from this week
GALLERY_NEW = 4
NEW_ADS = 8
MAX_FACTS = 8
BIG_LAUNCH = 10                    # a brand "launched many new ads" from this many
QUIET_DROP = 0.5                   # a brand went quiet when it runs under this share of last week's ads (and had at least QUIET_MIN)
QUIET_MIN = 10

CAVEATS = [
    "Each brand is a top slice by impressions (about 150 ads per search), so new and stopped counts move with the slice as well as with real launches and pauses.",
    "Start dates reset when a brand re-uploads an ad, so days running understates the age of re-uploaded ads.",
    "Format is video or image only: carousels count as image.",
]


def ad_url(library_id: str) -> str:
    return f"{LIBRARY}?id={quote(str(library_id), safe='')}"


def search_url(phrase: str) -> str:
    return f"{LIBRARY}?active_status=active&ad_type=all&country=US&q={quote(phrase)}&search_type=keyword_exact_phrase"


def domain_of(url: str | None) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    host = urlparse(u if "//" in u else "//" + u).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def page_of(url: str | None) -> str:
    """Landing domain plus path, tracking query and fragment removed."""
    u = (url or "").strip()
    if not u:
        return ""
    p = urlparse(u if "//" in u else "//" + u)
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return (host + p.path).rstrip("/")


def _date(raw: str | None, name: str) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise q.ApiError(400, f"{name} must be a date, YYYY-MM-DD") from None


def _format(raw: str | None) -> str | None:
    if raw in (None, "", "all"):
        return None
    if raw not in FORMATS:
        raise q.ApiError(400, "format must be video or image")
    return raw


def _fmt_ok(row: dict, fmt: str | None) -> bool:
    return fmt is None or (row.get("format") or "image") == fmt


def stored_weeks(conn) -> list[str]:
    return [r["week_start"].isoformat() for r in conn.execute("SELECT DISTINCT week_start FROM competitor_ads ORDER BY week_start DESC LIMIT 104").fetchall()]


def _pick_week(conn, raw_week: str | None) -> tuple[list[str], str | None, str | None]:
    """(stored weeks newest first, selected week, prior week). 404 when a given week is not stored."""
    weeks = stored_weeks(conn)
    wk = _date(raw_week, "week")
    if wk is None:
        sel = weeks[0] if weeks else None
    else:
        sel = wk.isoformat()
        if sel not in weeks:
            raise q.ApiError(404, f"No competitor snapshot for the week of {sel}.")
    older = [w for w in weeks if sel and w < sel]
    return weeks, sel, (older[0] if older else None)


def _chatter_table(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.competitor_posts') IS NOT NULL AS ok").fetchone()["ok"]


def _chatter(conn, week: str, brand: str | None, limit: int) -> list[dict]:
    sql = ("SELECT source, brand, title, url, subreddit, score, num_comments, posted_at, summary FROM competitor_posts WHERE week_start = %s"
           + (" AND brand = %s" if brand else "") + " ORDER BY score DESC NULLS LAST, posted_at DESC NULLS LAST LIMIT %s")
    params = (week, brand, limit) if brand else (week, limit)
    return [{"source": r["source"], "brand": r["brand"], "title": r["title"], "url": r["url"], "subreddit": r["subreddit"], "score": r["score"], "num_comments": r["num_comments"],
             "posted_at": r["posted_at"].isoformat() if r["posted_at"] else None, "summary": r["summary"]} for r in conn.execute(sql, params).fetchall()]


def _media_table(conn) -> bool:
    """True when the thumbnail table (competitor_ad_media) exists. Absent or empty means every thumb_url is null."""
    return conn.execute("SELECT to_regclass('public.competitor_ad_media') IS NOT NULL AS ok").fetchone()["ok"]


def _video_table(conn) -> bool:
    """True when the saved-video table (competitor_ad_video) exists. Absent or empty means every video_url is null."""
    return conn.execute("SELECT to_regclass('public.competitor_ad_video') IS NOT NULL AS ok").fetchone()["ok"]


def _ads_sql(media: bool, video: bool = False, extra_sel: str = "", extra_join: str = "") -> str:
    return ("SELECT a.library_id, a.brand, a.start_date, a.days_running, a.variant_count, a.format, a.hook, a.headline, a.primary_text, a.landing_url, a.concept_key, a.carried_from,\n"
            "       c.cluster, c.pain_point, c.mapped_product, c.is_new_angle, c.new_angle_label, "
            + ("(m.library_id IS NOT NULL) AS has_thumb, " if media else "false AS has_thumb, ")
            + ("(v.library_id IS NOT NULL) AS has_video" if video else "false AS has_video") + extra_sel + "\n"
            + "FROM competitor_ads a LEFT JOIN competitor_concept_class c ON c.concept_key = a.concept_key\n"
            + ("LEFT JOIN competitor_ad_media m ON m.library_id = a.library_id\n" if media else "")
            + ("LEFT JOIN competitor_ad_video v ON v.library_id = a.library_id\n" if video else "") + extra_join
            + "WHERE a.week_start = %s\n")


def _ads(conn, week: str | None, fmt: str | None, brand: str | None = None) -> list[dict]:
    if not week:
        return []
    sel, join = learn.sql_parts(conn)
    rows = conn.execute(_ads_sql(_media_table(conn), _video_table(conn), sel, join) + (" AND a.brand = %s" if brand else ""), (week, brand) if brand else (week,)).fetchall()
    return [r for r in rows if _fmt_ok(r, fmt)]


def _brand_rows(conn) -> list[dict]:
    return conn.execute("SELECT brand, product_type, search_phrases, active, last_ok_at, last_error, last_ad_count FROM competitor_brands ORDER BY brand").fetchall()


def _iso(v):
    return v.isoformat() if v else None


def _days(vs: list) -> tuple[float | None, int | None]:
    d = [x for x in vs if x is not None]
    return (round(sum(d) / len(d), 1), max(d)) if d else (None, None)


def _summaries(brands: list[dict], cur: list[dict], prev: list[dict] | None, latest: bool) -> list[dict]:
    by_cur, by_prev = collections.defaultdict(list), collections.defaultdict(list)
    for a in cur:
        by_cur[a["brand"]].append(a)
    for a in prev or []:
        by_prev[a["brand"]].append(a)
    out = []
    for b in brands:
        name = b["brand"]
        ads = by_cur.get(name, [])
        if not ads and not b["active"]:
            continue
        pids = {a["library_id"] for a in by_prev.get(name, [])}
        has_prior = prev is not None and name in by_prev
        ids = {a["library_id"] for a in ads}
        avg, mx = _days([a["days_running"] for a in ads])
        doms = collections.Counter(d for d in (domain_of(a["landing_url"]) for a in ads) if d)
        stale = any(a["carried_from"] for a in ads) or (latest and bool(b["last_error"]))
        out.append({
            "brand": name, "product_type": b["product_type"], "active_ads": len(ads),
            "change": (len(ads) - len(pids)) if has_prior else None,
            "new_ads": len(ids - pids) if has_prior else None,
            "stopped_ads": len(pids - ids) if has_prior else None,
            "avg_days_running": avg, "max_days_running": mx,
            "video_share": round(sum(1 for a in ads if a["format"] == "video") / len(ads), 3) if ads else None,
            "top_landing_domain": doms.most_common(1)[0][0] if doms else None,
            "is_stale": stale, "last_ok_at": _iso(b["last_ok_at"]), "last_error": b["last_error"] if latest else None,
            "thumbs": _thumbs(ads), "top_angle": _top_angle(ads),
        })
    return out


def media_url(library_id: str) -> str:
    return f"/api/competitors/media/{quote(str(library_id), safe='')}"


def video_url(library_id: str) -> str:
    return f"/api/competitors/video/{quote(str(library_id), safe='')}"


def _video(a: dict) -> str | None:
    """The saved playable copy of an ad, only when one is stored (and the ad is a video)."""
    return video_url(a["library_id"]) if a.get("has_video") and a.get("format") == "video" else None


def _thumb(a: dict) -> str | None:
    return media_url(a["library_id"]) if a.get("has_thumb") else None


def _angle_of(a: dict) -> tuple | None:
    """(cluster, pain_point) for an ad that is classified into our taxonomy, else None (out of category or not classified yet)."""
    if a.get("mapped_product") == "none" or not (a.get("pain_point") or a.get("cluster")):
        return None
    return (a.get("cluster"), a.get("pain_point"))


def _top_angle(ads: list[dict]) -> dict | None:
    g: dict[tuple, dict] = {}
    for a in ads:
        k = _angle_of(a)
        if k is None:
            continue
        e = g.setdefault(k, {"label": k[1] or k[0], "cluster": k[0], "ads": 0, "is_new_angle": False})
        e["ads"] += 1
        e["is_new_angle"] = e["is_new_angle"] or bool(a.get("is_new_angle"))
    return sorted(g.values(), key=lambda e: (-e["ads"], e["label"]))[0] if g else None


def _thumbs(ads: list[dict]) -> list[dict]:
    """Up to three of a brand's longest-running distinct ads for the tile strip, ads with a stored image first."""
    lo = _longest(ads)
    pick = ([a for a in lo if a.get("has_thumb")] + [a for a in lo if not a.get("has_thumb")])[:3]
    return [{"library_id": a["library_id"], "thumb_url": _thumb(a), "video_url": _video(a), "hook": (a["hook"] or a["headline"] or "")[:90], "format": a["format"]} for a in pick]


def _dedupe(ads: list[dict]) -> list[dict]:
    seen, out = set(), []
    for a in ads:
        if a["concept_key"] in seen:
            continue
        seen.add(a["concept_key"])
        out.append(a)
    return out


def _card(a: dict, badge: str | None = None, reason: str | None = None) -> dict:
    """An ad as the cards and the lightbox show it: copy, landing page, links and (only when stored) a thumbnail."""
    return {"library_id": a["library_id"], "brand": a["brand"], "hook": a["hook"] or a["headline"] or "", "headline": a.get("headline"), "primary_text": (a.get("primary_text") or "")[:700],
            "format": a["format"], "days_running": a["days_running"], "variant_count": a["variant_count"], "landing_url": a.get("landing_url"), "landing_domain": domain_of(a.get("landing_url")),
            "thumb_url": _thumb(a), "video_url": _video(a), "ad_library_url": ad_url(a["library_id"]), "angle": " / ".join(x for x in (a.get("cluster"), a.get("pain_point")) if x) or None,
            "is_new_angle": bool(a.get("is_new_angle")), "new_angle_label": a.get("new_angle_label"), "badge": badge, "reason": reason, **learn.card_extras(a)}


def _days_badge(a: dict) -> str | None:
    return f"{a['days_running']} days running" if a.get("days_running") is not None else None


def _ad_row(a: dict) -> dict:
    return {"library_id": a["library_id"], "brand": a["brand"], "hook": a["hook"] or a["headline"] or "", "headline": a["headline"], "format": a["format"], "days_running": a["days_running"],
            "start_date": _iso(a["start_date"]), "variant_count": a["variant_count"], "landing_domain": domain_of(a["landing_url"]), "cluster": a["cluster"], "pain_point": a["pain_point"],
            "ad_library_url": ad_url(a["library_id"])}


def _longest(ads: list[dict]) -> list[dict]:
    return _dedupe(sorted(ads, key=lambda a: (a["days_running"] is None, -(a["days_running"] or 0), a["library_id"])))


def _facts(summ: list[dict], cur: list[dict], prev: list[dict] | None, first: bool) -> list[dict]:
    facts: list[dict] = []
    if not first:
        launches = sorted((s for s in summ if (s["new_ads"] or 0) >= BIG_LAUNCH), key=lambda s: -s["new_ads"])[:3]
        for s in launches:
            facts.append({"kind": "launch", "brand": s["brand"], "text": f"{s['brand']} started {s['new_ads']} new ads and now runs {s['active_ads']}."})
        quiet = sorted((s for s in summ if s["change"] is not None and not s["is_stale"] and (s["active_ads"] + (-s["change"])) >= QUIET_MIN and s["active_ads"] < QUIET_DROP * (s["active_ads"] - s["change"])),
                       key=lambda s: s["change"])[:2]
        for s in quiet:
            was = s["active_ads"] - s["change"]
            facts.append({"kind": "quiet", "brand": s["brand"], "text": f"{s['brand']} went quiet: {s['active_ads']} ads now, down from {was} last week."})
    for s in [s for s in summ if s["is_stale"]][:2]:
        facts.append({"kind": "stale", "brand": s["brand"], "text": f"{s['brand']} could not be read this week, so its numbers are from an earlier week."})
    prev_keys = {a["concept_key"] for a in prev or []}
    angles: dict[str, dict] = {}
    for a in cur:
        if a["is_new_angle"] and a["new_angle_label"]:
            g = angles.setdefault(a["new_angle_label"].strip().lower(), {"label": a["new_angle_label"].strip(), "ads": 0, "brands": collections.Counter(), "fresh": False})
            g["ads"] += 1
            g["brands"][a["brand"]] += 1
            g["fresh"] = g["fresh"] or (not first and a["concept_key"] not in prev_keys)
    ranked = sorted(angles.values(), key=lambda g: (not g["fresh"], -g["ads"], g["label"]))[:3]
    for g in ranked:
        top = g["brands"].most_common(1)[0][0]
        facts.append({"kind": "angle", "brand": top, "text": f"Angle Lonvera does not run: {g['label']} ({g['ads']} ads, mostly {top})" + (", new this week." if g["fresh"] else ".")})
    for a in _longest(cur)[:3]:
        if a["days_running"] is not None:
            hook = (a["hook"] or a["headline"] or "").strip()
            hook = (hook[:90] + "...") if len(hook) > 90 else hook
            facts.append({"kind": "longest", "brand": a["brand"], "library_id": a["library_id"], "ad_library_url": ad_url(a["library_id"]),
                          "text": f"{a['brand']}: {a['days_running']} days running" + (f", \"{hook}\"" if hook else "") + "."})
    return facts[:MAX_FACTS]


SPOT_MAX = 12
SPOT_MIN = 8
SPOT_PROVEN = 5            # longest-running proven ads (at most two per brand)
SPOT_ANGLE = 4
SPOT_SURGE_BRANDS = 3
PROVEN_DAYS = 30           # "proven" = kept on for at least this many days
SURGE_NEW = 3              # a brand "surged" with at least this many new ads and at least SURGE_SHARE of what it runs now
SURGE_SHARE = 0.3


def _spotlight(summ: list[dict], cur: list[dict], prev: list[dict] | None, first: bool) -> list[dict]:
    """8 to 12 ads worth a look: proven long-runners (max 2 per brand), ads running an angle Lonvera does not run, new ads from brands that surged. Interleaved so the rail mixes the three."""
    if not cur:
        return []
    prev_ids = {a["library_id"] for a in prev or []}
    longest = _longest(cur)
    seen: set[str] = set()
    per_brand: collections.Counter = collections.Counter()

    def take(a, reason, badge, out):
        if a["library_id"] in seen or per_brand[a["brand"]] >= 2:
            return False
        seen.add(a["library_id"])
        per_brand[a["brand"]] += 1
        out.append(_card(a, badge, reason))
        return True

    # surge first, then angle, then proven: each claims its slots before the long-runners fill the per-brand cap of two
    surge: list[dict] = []
    if not first:
        hot = sorted((s for s in summ if (s["new_ads"] or 0) >= SURGE_NEW and s["new_ads"] >= SURGE_SHARE * s["active_ads"] and not s["is_stale"]), key=lambda s: (-s["new_ads"], s["brand"]))[:SPOT_SURGE_BRANDS]
        for s in hot:
            fresh = _dedupe(sorted((a for a in cur if a["brand"] == s["brand"] and a["library_id"] not in prev_ids), key=lambda a: (-(a["variant_count"] or 1), a["library_id"])))
            for a in fresh[:1]:                                         # one card per surging brand: the new ad with the most copies behind it
                take(a, "surge", "New this week", surge)
    angle: list[dict] = []
    cands = sorted((a for a in longest if a["is_new_angle"]), key=lambda a: (a["library_id"] in prev_ids or first, -(a["days_running"] or 0), a["library_id"]))
    used_brands: set[str] = set()
    for a in cands:
        if len(angle) >= SPOT_ANGLE:
            break
        if a["brand"] not in used_brands and take(a, "new_angle", "Angle Lonvera does not run", angle):
            used_brands.add(a["brand"])
    proven: list[dict] = []
    for a in longest:
        if len(proven) >= SPOT_PROVEN:
            break
        if a["days_running"] is not None and a["days_running"] >= PROVEN_DAYS:
            take(a, "proven", _days_badge(a), proven)
    out: list[dict] = []
    for i in range(max(len(proven), len(angle), len(surge))):
        for grp in (proven, angle, surge):
            if i < len(grp):
                out.append(grp[i])
    for a in longest:                                                   # top up with more long-runners until the rail is worth scrolling
        if len(out) >= SPOT_MIN:
            break
        take(a, "longest", _days_badge(a), out)
    return out[:SPOT_MAX]


def _angle_chart(cur: list[dict]) -> list[dict]:
    g: dict[tuple, dict] = {}
    for a in cur:
        k = _angle_of(a)
        if k is None:
            continue
        e = g.setdefault(k, {"label": k[1] or k[0], "cluster": k[0], "pain_point": k[1], "ads": 0, "brands": set(), "is_new_angle": False, "new_angle_label": None})
        e["ads"] += 1
        e["brands"].add(a["brand"])
        if a["is_new_angle"]:
            e["is_new_angle"], e["new_angle_label"] = True, a["new_angle_label"]
    rows = sorted(g.values(), key=lambda e: (-e["ads"], e["label"]))[:10]
    return [{**e, "brands": len(e["brands"])} for e in rows]


def _pulse(summ: list[dict], cur: list[dict], first: bool) -> dict:
    lo = [a for a in _longest(cur) if a["days_running"] is not None][:1]
    news = [s["new_ads"] for s in summ if s["new_ads"] is not None]
    return {"brands": len(summ), "active_ads": sum(s["active_ads"] for s in summ), "new_ads": (None if first or not news else sum(news)),
            "longest_days": lo[0]["days_running"] if lo else None, "longest_brand": lo[0]["brand"] if lo else None,
            "longest_ad_library_url": ad_url(lo[0]["library_id"]) if lo else None}


def _prepare(cur, prev, ctx):
    """Merge model-read tags over the text class, then apply the tag filters (only when the tags table exists). Returns (all rows, filtered rows, filtered prior rows, active filters)."""
    cur = [learn.merge_tags(a) for a in cur]
    prev = None if prev is None else [learn.merge_tags(a) for a in prev]
    f = (ctx.get("filters") or {}) if ctx.get("tags_table") else {}
    if not f:
        return cur, cur, prev, f
    return cur, [a for a in cur if learn.tag_ok(a, f)], (None if prev is None else [a for a in prev if learn.tag_ok(a, f)]), f


def _lp_visible(ctx, cur, fmt, f):
    return learn.restrict(ctx.get("lp_rows") or [], cur, bool(f or fmt), bool(ctx.get("lp_linked")))


def payload_from(weeks: list[str], sel: str | None, prior: str | None, fmt: str | None, brands: list[dict], cur: list[dict], prev: list[dict] | None, ctx: dict | None = None) -> dict:
    """The /api/competitors body from already-read rows (pure: the UI mock server uses it too). ctx (all optional): tags_table, week_tagged, filters, lonvera, lp_rows, lp_linked."""
    ctx = ctx or {}
    out = {"weeks": weeks, "week": sel, "prior_week": prior, "format": fmt or "all", "first_snapshot": bool(sel) and prior is None, "caveats": CAVEATS, "brands": [], "changes": [], "notice": None,
           "pulse": {"brands": 0, "active_ads": 0, "new_ads": None, "longest_days": None, "longest_brand": None, "longest_ad_library_url": None}, "spotlight": [], "angle_chart": []}
    if not sel:
        out["notice"] = "No competitor snapshot has been stored yet. The first one is written on a Sunday."
        return out
    all_cur, cur, prev, f = _prepare(cur, prev, ctx)
    summ = _summaries(brands, cur, prev, sel == weeks[0])
    out["brands"] = summ
    out["changes"] = _facts(summ, cur, prev, prior is None)
    out["pulse"] = _pulse(summ, cur, prior is None)
    out["spotlight"] = _spotlight(summ, cur, prev, prior is None)
    out["angle_chart"] = _angle_chart(cur)
    if prior is None:
        out["notice"] = "First snapshot: changes against the week before start next week."
    if ctx.get("tags_table"):
        tagged = ctx.get("week_tagged")
        tagged = sum(1 for a in all_cur if a.get("tg_present")) if tagged is None else tagged
        out["tags"] = {"empty": tagged == 0, "options": learn.options(all_cur), "active": {learn.FILTER_PARAM[c]: v for c, v in f.items()}, "note": learn.NOTE}
        lr = learn.learning(cur, ctx.get("lonvera"))
        if lr:
            out["learn"] = lr
    lp = learn.lp_patterns(_lp_visible(ctx, cur, fmt, f))
    if lp:
        out["lp_patterns"] = lp
    return out


def _ctx(conn, sel: str | None, filters: dict | None, brand: str | None = None) -> dict:
    ctx: dict = {"tags_table": False, "filters": filters or {}}
    if not sel:
        return ctx
    if learn.exists(conn, "competitor_ad_tags"):
        ctx["tags_table"] = True
        ctx["week_tagged"] = conn.execute("SELECT count(*) AS n FROM competitor_ads a JOIN competitor_ad_tags t ON t.library_id = a.library_id WHERE a.week_start = %s", (sel,)).fetchone()["n"]
        if ctx["week_tagged"] and not brand:
            ctx["lonvera"] = learn.lonvera_shares(conn)
    ctx["lp_rows"] = learn.lp_rows(conn, sel, brand)
    ctx["lp_linked"] = bool(ctx["lp_rows"]) and learn.lp_linked(conn, sel)
    return ctx


def payload(conn, raw_week: str | None, raw_format: str | None, filters: dict | None = None) -> dict:
    fmt = _format(raw_format)
    weeks, sel, prior = _pick_week(conn, raw_week)
    out = payload_from(weeks, sel, prior, fmt, _brand_rows(conn), _ads(conn, sel, fmt), _ads(conn, prior, fmt) if prior else None, _ctx(conn, sel, filters))
    if sel and _chatter_table(conn):
        out["chatter"] = _chatter(conn, sel, None, 10)
    return out


def detail_from(b: dict, sel: str | None, prior: str | None, fmt: str | None, cur: list[dict], prev: list[dict] | None, ctx: dict | None = None) -> dict:
    """The /api/competitors/<brand> body from already-read rows (pure). ctx as for payload_from; lp_rows are this brand's landing pages."""
    ctx = ctx or {}
    phrase = (b["search_phrases"] or [b["brand"]])[0]
    out = {"brand": b["brand"], "product_type": b["product_type"], "week": sel, "prior_week": prior, "format": fmt or "all", "first_snapshot": bool(sel) and prior is None,
           "brand_ad_library_url": search_url(phrase), "gallery": [], "longest_running": [], "new_this_week": [], "angles": [], "landing_pages": [], "caveats": CAVEATS[1:2]}
    if not sel:
        return out
    cur, cur_f, prev, f = _prepare(cur, prev, ctx)
    cur = cur_f
    mix = learn.tag_mix(cur) if ctx.get("tags_table") else None
    if mix:
        out["tag_mix"] = mix
    out["longest_running"] = [_ad_row(a) for a in _longest(cur)[:TOP_ADS]]
    fresh_cards: list[dict] = []
    if prev:
        pids = {a["library_id"] for a in prev}
        fresh = [a for a in cur if a["library_id"] not in pids]
        fresh.sort(key=lambda a: (-(a["variant_count"] or 1), a["days_running"] if a["days_running"] is not None else 10**6, a["library_id"]))
        out["new_this_week"] = [_ad_row(a) for a in _dedupe(fresh)[:NEW_ADS]]
        out["new_count"] = len(fresh)
        fresh_cards = _dedupe(fresh)[:GALLERY_NEW]
    fresh_keys = {a["concept_key"] for a in fresh_cards}
    old = [a for a in _longest(cur) if a["concept_key"] not in fresh_keys][:GALLERY_MAX - len(fresh_cards)]
    out["gallery"] = [_card(a, _days_badge(a), "longest") for a in old] + [_card(a, "New this week", "new") for a in fresh_cards]
    angles: dict[tuple, dict] = {}
    for a in cur:
        if a["mapped_product"] == "none":
            key, label = ("none",), "Out of category"
        elif a["pain_point"] or a["cluster"]:
            key, label = (a["cluster"], a["pain_point"]), None
        else:
            key, label = ("unclassified",), "Not classified yet"
        g = angles.setdefault(key, {"cluster": a["cluster"] if label is None else label, "pain_point": a["pain_point"] if label is None else None, "ads": 0, "concepts": set(),
                                    "is_new_angle": False, "new_angle_label": None, "product": a["mapped_product"]})
        g["ads"] += 1
        g["concepts"].add(a["concept_key"])
        if a["is_new_angle"]:
            g["is_new_angle"], g["new_angle_label"] = True, a["new_angle_label"]
    out["angles"] = sorted(({**g, "concepts": len(g["concepts"])} for g in angles.values()), key=lambda g: -g["ads"])[:10]
    pages = collections.Counter(p for p in (page_of(a["landing_url"]) for a in cur) if p)
    out["landing_pages"] = [{"page": p, "ads": n} for p, n in sorted(pages.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
    cards = [learn.lp_card(r, sel) for r in _lp_visible(ctx, cur, fmt, f)][:8]
    if cards:
        out["landing_pages"] = cards                   # tagged landing pages replace the plain counts
    return out


def brand_detail(conn, brand: str, raw_week: str | None, raw_format: str | None, filters: dict | None = None) -> dict:
    fmt = _format(raw_format)
    b = conn.execute("SELECT brand, product_type, search_phrases FROM competitor_brands WHERE brand = %s", (brand,)).fetchone()
    if b is None:
        raise q.ApiError(404, "Unknown competitor brand.")
    weeks, sel, prior = _pick_week(conn, raw_week)
    out = detail_from(b, sel, prior, fmt, _ads(conn, sel, fmt, brand), _ads(conn, prior, fmt, brand) if prior else None, _ctx(conn, sel, filters, brand))
    if sel and _chatter_table(conn):
        out["chatter"] = _chatter(conn, sel, brand, 5)
    return out
