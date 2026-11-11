"""Landing pages (read-only): GET /api/landing-pages lists every page with its current copy summary and its top ads; GET /api/landing-pages/<id> returns the full stored copy.
All data comes from migration 207 (public.landing_pages, landing_page_versions, landing_page_top_ads); see docs/LANDING_PAGES.md. SQL lives here, the route in app/api.py."""
from __future__ import annotations

from .queries import ApiError

TOP_N_DEFAULT, TOP_N_MAX = 5, 25


def _f(x):
    return None if x is None else float(x)


def _top_ads(conn, page_ids: list[int], window: int, n: int) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in page_ids}
    if not page_ids:
        return out
    rows = conn.execute(
        """SELECT landing_page_id, ad_id, ad_name, spend, purchases, revenue, roas, rank_by_spend FROM landing_page_top_ads
           WHERE landing_page_id = ANY(%s) AND window_days = %s AND rank_by_spend <= %s ORDER BY landing_page_id, rank_by_spend""", (page_ids, window, n)).fetchall()
    for r in rows:
        out[r["landing_page_id"]].append({"rank": r["rank_by_spend"], "ad_id": r["ad_id"], "ad_name": r["ad_name"], "spend": _f(r["spend"]), "purchases": _f(r["purchases"]),
                                          "revenue": _f(r["revenue"]), "roas": _f(r["roas"])})
    return out


def _window(args) -> int:
    w = (args.get("window") or "30").strip()
    if w not in ("7", "30"):
        raise ApiError(400, "window must be 7 or 30")
    return int(w)


def _top_n(args) -> int:
    try:
        n = int(args.get("top") or TOP_N_DEFAULT)
    except ValueError:
        raise ApiError(400, "top must be a whole number") from None
    if not 1 <= n <= TOP_N_MAX:
        raise ApiError(400, f"top must be between 1 and {TOP_N_MAX}")
    return n


def list_pages(conn, args) -> dict:
    """Pages ordered by spend in the window (pages with no spend in it last), each with its current title, word count, price count and top ads."""
    window, n = _window(args), _top_n(args)
    rows = conn.execute(
        """SELECT p.id, p.url, p.host, p.path, p.first_seen_at, p.last_seen_at, p.last_fetched_at, p.last_status, p.last_error,
                  v.id AS version_id, v.fetched_at AS version_first_seen, v.title, v.word_count, v.rendered_in_browser,
                  jsonb_array_length(v.prices) AS price_count, jsonb_array_length(v.ctas) AS cta_count,
                  (SELECT count(*) FROM landing_page_versions x WHERE x.landing_page_id = p.id) AS versions,
                  (SELECT count(DISTINCT a.ad_id) FROM landing_page_ads a WHERE a.landing_page_id = p.id) AS ads_mapped,
                  COALESCE((SELECT sum(t.spend) FROM landing_page_top_ads t WHERE t.landing_page_id = p.id AND t.window_days = %s), 0) AS spend
           FROM landing_pages p LEFT JOIN landing_page_versions v ON v.id = p.current_version_id
           WHERE p.active ORDER BY spend DESC, p.url""", (window,)).fetchall()
    tops = _top_ads(conn, [r["id"] for r in rows], window, n)
    pages = [{"id": r["id"], "url": r["url"], "host": r["host"], "path": r["path"], "first_seen_at": r["first_seen_at"].isoformat(), "last_seen_at": r["last_seen_at"].isoformat(),
              "last_fetched_at": r["last_fetched_at"].isoformat() if r["last_fetched_at"] else None, "last_status": r["last_status"], "last_error": r["last_error"],
              "title": r["title"], "word_count": r["word_count"], "rendered_in_browser": r["rendered_in_browser"], "price_count": r["price_count"], "cta_count": r["cta_count"],
              "versions": r["versions"], "ads_mapped": r["ads_mapped"], "spend": _f(r["spend"]), "top_ads": tops[r["id"]]} for r in rows]
    return {"window_days": window, "top": n, "pages": pages,
            "note": "Spend is the sum over the page's ads; an ad that lists several links counts in full under each page. Purchases and revenue are Meta-reported."}


def page_detail(conn, page_id: int, args) -> dict:
    """One page: every stored version (newest first; the current one carries the full copy) and the top ads in the window."""
    window, n = _window(args), _top_n(args)
    p = conn.execute("SELECT id, url, host, path, first_seen_at, last_seen_at, last_fetched_at, last_status, last_error, current_version_id FROM landing_pages WHERE id = %s", (page_id,)).fetchone()
    if not p:
        raise ApiError(404, "landing page not found")
    vs = conn.execute(
        """SELECT id, content_hash, fetched_at, http_status, final_url, title, meta_description, rendered_in_browser, word_count, blocks, ctas, prices, guarantees, reviews, images
           FROM landing_page_versions WHERE landing_page_id = %s ORDER BY fetched_at DESC, id DESC""", (page_id,)).fetchall()
    versions = []
    for v in vs:
        d = {"id": v["id"], "first_seen_at": v["fetched_at"].isoformat(), "http_status": v["http_status"], "final_url": v["final_url"], "title": v["title"],
             "meta_description": v["meta_description"], "rendered_in_browser": v["rendered_in_browser"], "word_count": v["word_count"], "is_current": v["id"] == p["current_version_id"]}
        if d["is_current"]:
            d.update({k: v[k] for k in ("blocks", "ctas", "prices", "guarantees", "reviews", "images")})
        versions.append(d)
    return {"id": p["id"], "url": p["url"], "host": p["host"], "path": p["path"], "first_seen_at": p["first_seen_at"].isoformat(), "last_seen_at": p["last_seen_at"].isoformat(),
            "last_fetched_at": p["last_fetched_at"].isoformat() if p["last_fetched_at"] else None, "last_status": p["last_status"], "last_error": p["last_error"],
            "window_days": window, "top_ads": _top_ads(conn, [p["id"]], window, n)[p["id"]], "versions": versions}
