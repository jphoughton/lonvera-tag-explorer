"""Google from Upstack: campaign type from the name, channel-feed shaping, the empty-day guard. No database, no network."""
import datetime as dt

import pytest

from etl import db, upstack_google as G

D = dt.date


@pytest.mark.parametrize("name,typ", [
    ("Standard Shopping | All Products | US | tROAS 190% | Branded | Hot", "Shopping"),
    ("SS | US | Vital Bella BOF  | MTS BG/PM | 10/20/2025", "Shopping"),
    ("PMax | Vita Bull Products | Male & Unknown Audience | MOF | tROAS > Max Conv | US | Mixed", "Performance Max"),
    ("Pmax | US | Vital Belle | MTS BG/PM | 09/09/2025", "Performance Max"),
    ("Brand Search | US | Hot | tROAS 160%", "Search"),
    ("Prospecting Search | Beef Liver Phrase/Broad | TROAS 180% | US | Cold", "Search"),
    ("Competitors Search | Black Bull | Raging Bull | US | Cold | tROAS Removed", "Search"),
    ("IL | Search | MOF | Vital Belle | mConv | US", "Search"),
    ("DG15 | Vital Bull | Rank 7,8,9,10 | Video 9x16 | PDPVitalBull | VTC On | US | Cold | BNDL", "Demand Gen"),
    ("DG | US | Remarketing | MTS BG/PM | 09/09/2025", "Demand Gen"),
    ("IL | Demand Gen |  Beef Liver | mConv | US", "Demand Gen"),
    ("Prospecting | Top 3 Products | US | Cold | Max Conv > tROAS 130%", "Other"),     # says no type: never guessed
    ("", "Other"),
])
def test_campaign_type_from_the_name(name, typ):
    assert G.campaign_type(name) == typ


def test_channel_google_keeps_google_campaigns_only_and_drops_empty_days():
    res = [{"campaign": "111", "campaignName": "Brand Search | US", "basicDetails": {"resourceSource": "googleAdsIntegration", "status": "ENABLED", "bidStrategy": "TARGET_ROAS"},
            "data": {"primary": [{"dt": "2026-10-30T00:00:00Z", "channel.spend": "10.5", "channel.purchases": 2, "channel.purchases_value": "150", "channel.clicks": 30, "channel.impressions": 400},
                                 {"dt": "2026-10-31", "channel.spend": 0}]}},
           {"campaign": "222", "campaignName": "Meta one", "basicDetails": {"resourceSource": "facebookIntegration"}, "data": {"primary": [{"dt": "2026-10-30", "channel.spend": 99}]}},
           {"campaign": "", "campaignName": "No id", "basicDetails": {"resourceSource": "googleAdsIntegration"}, "data": {"primary": []}}]
    camps, daily = G.channel_google(res)
    assert camps == [("111", "Brand Search | US", "ENABLED", "TARGET_ROAS", "Search")]
    assert daily == [(D(2026, 10, 30), "111", 10.5, 400.0, 30.0, 2.0, 150.0)]


def test_check_days_refuses_an_empty_answer_on_a_day_with_real_google_spend():
    daily = [(D(2026, 10, 30), "1", 500.0, 0, 0, 0, 0), (D(2026, 10, 31), "1", 50.0, 0, 0, 0, 0)]
    G.check_days({D(2026, 10, 30): [["Brand Search", 1, 10, 1, 10]], D(2026, 10, 31): []}, daily)       # a quiet low-spend day is fine
    with pytest.raises(db.JobFailed):
        G.check_days({D(2026, 10, 30): []}, daily)                                                          # $500 of spend and no orders at all: Upstack answered with nothing
    G.check_days({D(2025, 5, 1): []}, [(D(2025, 5, 1), "1", 900.0, 0, 0, 0, 0)])                            # before Upstack had any attribution an empty day is expected
