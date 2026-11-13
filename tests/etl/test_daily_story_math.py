"""Daily story arithmetic: ratios, z-scores, the exact log decomposition and the exact mix-versus-rate split (no database)."""
import math
import random

import pytest

from etl import daily_story_math as M


def test_ratio_is_none_safe():
    assert M.ratio(1, 4) == 0.25
    assert M.ratio(None, 4) is None and M.ratio(1, None) is None and M.ratio(1, 0) is None
    assert M.pct_change(110, 100) == pytest.approx(0.10)
    assert M.pct_change(1, 0) is None


def test_median_mean_stdev_skip_none():
    assert M.median([1, None, 3, 100]) == 3
    assert M.median([None]) is None
    assert M.mean([1, None, 3]) == 2
    assert M.stdev([1]) is None
    assert M.stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)


def test_zscore_needs_enough_history_and_nonzero_noise():
    hist = [1.0, 2.0] * 6
    z = M.zscore(3.0, 1.5, hist)
    assert z == pytest.approx((3.0 - 1.5) / M.stdev(hist))
    assert M.zscore(3.0, 1.5, hist[:5]) is None                 # fewer than MIN_NOISE_DAYS values
    assert M.zscore(3.0, 1.5, [2.0] * 12) is None               # zero noise
    assert M.zscore(None, 1.5, hist) is None


def test_flag_threshold():
    assert M.flag(2.0) == "high" and M.flag(-2.5) == "low" and M.flag(1.99) is None and M.flag(None) is None


def test_log_decompose_adds_back_exactly():
    # metric = a * b / c
    res = M.log_decompose([("a", 12.0, 10.0, +1), ("b", 0.5, 0.4, +1), ("c", 3.0, 2.5, -1)])
    md, mb = 12.0 * 0.5 / 3.0, 10.0 * 0.4 / 2.5
    assert res["total_log"] == pytest.approx(math.log(md / mb), abs=1e-12)
    assert sum(t["log"] for t in res["terms"]) == pytest.approx(res["total_log"], abs=1e-12)
    assert res["pct_change"] == pytest.approx(md / mb - 1, abs=1e-12)
    assert sum(t["pct_points"] for t in res["terms"]) == pytest.approx(res["pct_change"], abs=1e-12)
    assert sum(t["share"] for t in res["terms"]) == pytest.approx(1.0, abs=1e-12)
    # the sign: a higher c lowers the metric
    c = next(t for t in res["terms"] if t["name"] == "c")
    assert c["log"] < 0


def test_log_decompose_refuses_missing_or_nonpositive_values():
    assert M.log_decompose([("a", None, 1.0, 1)]) is None
    assert M.log_decompose([("a", 0.0, 1.0, 1)]) is None
    assert M.log_decompose([("a", 1.0, -1.0, 1)]) is None


def test_log_decompose_no_move_has_zero_terms():
    res = M.log_decompose([("a", 2.0, 2.0, 1), ("b", 3.0, 3.0, -1)])
    assert res["total_log"] == 0 and all(t["pct_points"] == 0 for t in res["terms"])


def test_mix_rate_hand_example():
    # two segments: A earns 1.0 per dollar, B earns 0.5. Baseline spends 50/50 (R0 = 0.75). Day spends 80% on A at the same rates: pure mix.
    base = {"A": (50.0, 50.0), "B": (50.0, 25.0)}
    day = {"A": (80.0, 80.0), "B": (20.0, 10.0)}
    r = M.mix_rate(day, base)
    assert r["base"] == pytest.approx(0.75) and r["value"] == pytest.approx(0.9)
    assert r["rate"] == pytest.approx(0.0, abs=1e-12) and r["mix"] == pytest.approx(0.15)
    # pure rate: same shares, A earns 20% more
    day2 = {"A": (50.0, 60.0), "B": (50.0, 25.0)}
    r2 = M.mix_rate(day2, base)
    assert r2["mix"] == pytest.approx(0.0, abs=1e-12) and r2["rate"] == pytest.approx(0.10)


@pytest.mark.parametrize("seed", range(8))
def test_mix_rate_adds_back_exactly_on_random_segments(seed):
    R = random.Random(seed)
    keys = [f"s{i}" for i in range(R.randint(2, 9))]
    def side():
        return {k: (R.choice([0.0, R.uniform(1, 500)]), R.uniform(0, 600)) for k in keys}
    day, base = side(), side()
    day["s0"] = (R.uniform(1, 100), 50.0)                            # both sides always have some spend
    base["s0"] = (R.uniform(1, 100), 40.0)
    for k in keys:                                                   # a segment with no spend has no revenue
        for side_ in (day, base):
            if side_[k][0] == 0.0:
                side_[k] = (0.0, 0.0)
    r = M.mix_rate(day, base)
    assert r["mix"] + r["rate"] == pytest.approx(r["change"], abs=1e-9)
    assert sum(x["total"] for x in r["rows"]) == pytest.approx(r["change"], abs=1e-9)
    assert sum(x["share"] for x in r["rows"]) == pytest.approx(1.0, abs=1e-9)


def test_mix_rate_none_without_spend():
    assert M.mix_rate({"A": (0.0, 0.0)}, {"A": (1.0, 1.0)}) is None


def test_bucket_edges():
    assert M.bucket(5, (10, 20), ("a", "b", "c")) == "a"
    assert M.bucket(10, (10, 20), ("a", "b", "c")) == "b"
    assert M.bucket(99, (10, 20), ("a", "b", "c")) == "c"
