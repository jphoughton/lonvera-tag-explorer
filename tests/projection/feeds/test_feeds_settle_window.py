"""Verify tolerates late restatements: a day inside a feed's settle window can only be 'soft', never 'fail'.

Google Ads restates conversion value for days after the day (Upstack's Google purchase value for 2026-10-30 moved +7.8% five days later and turned the
nightly verify red), so the platform feed must settle for at least a week, like the Amazon feeds.
"""
from etl import feeds


def test_platform_feed_settles_google_conversion_restatements():
    assert feeds.registry()["platform"].settle_days >= 7


def test_amazon_ads_feeds_keep_their_two_week_settle_window():
    reg = feeds.registry()
    assert reg["amazon"].settle_days >= 14
    assert reg["units_amazon"].settle_days >= 14
