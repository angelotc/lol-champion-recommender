import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import builds
from data.lolalytics import _qwik_build


def raw(wr, n, boots, runes=None):
    return {
        "header": {"cid": 89, "lane": "support", "n": n, "wr": wr},
        "summary": {"pick": {"runes": {"wr": wr, "n": n, "set": {"pri": [8439, 8401, 8473, 8242], "sec": [8347, 8306], "mod": [5007, 5001, 5001]}}}},
        "runes": {"stats": runes or {"8439": [[90, wr, n]], "5001f": [[60, wr, n // 2]]}},
        "boots": boots, "spells": [["4_14", wr, 60, n]], "skillOrder": [["QWE", wr, 80, n]],
    }


def test_parse_keeps_sections_and_hides_noise():
    b = builds.parse(raw(51.0, 10000, [[3047, 52.0, 50, 5000, 12], [1001, 30.0, 6, 600, 5], [3111, 51.0, 0.5, 50, 14]]))
    assert [o.key for o in b.sections["boots"]] == ["3047"]  # plain Boots and <1% picks hidden
    assert b.pages["pick"].primary[0] == 8439
    assert builds.shard_keys(b.pages["pick"].shards) == ["5007", "5001f", "5001"]
    assert b.sections["spells"][0].ids == [4, 14]
    assert builds.parse({}) is None


def test_matchup_delta_is_relative_to_each_baseline():
    base = builds.parse(raw(50.0, 50000, [[3047, 52.0, 50, 25000, 12], [3111, 50.0, 30, 15000, 14]]))
    # vs this enemy you win more overall (54%): Steelcaps keeps its usual +2 edge, Mercs gains +3
    vs = builds.parse(raw(54.0, 5000, [[3047, 56.0, 50, 2500, 12], [3111, 57.0, 30, 1500, 14]]))
    builds.add_matchup(base, vs, enemy=202, lane="bottom", lane_weight=1.0, k=300, min_games=100)
    steel, mercs = base.sections["boots"]
    assert steel.vs[0].delta == pytest.approx(0.0)
    assert mercs.vs[0].delta == pytest.approx(3.0)
    assert mercs.matchup == pytest.approx(3.0 * 1500 / 1800)
    assert builds.best_vs(base.sections["boots"]).key == "3111"


def test_matchup_ignores_small_samples():
    base = builds.parse(raw(50.0, 50000, [[3047, 52.0, 50, 25000, 12]]))
    vs = builds.parse(raw(54.0, 5000, [[3047, 70.0, 50, 40, 12]]))
    builds.add_matchup(base, vs, 202, "bottom", 1.0, 300, min_games=100)
    assert base.sections["boots"][0].vs == [] and builds.best_vs(base.sections["boots"]) is None


def test_qwik_state_decoding():
    # objs: container values are base-36 indexes; "\x12<idx>" strings wrap a reference
    objs = [
        {"header": "1", "summary": "2", "runes": "3", "spells": "4", "boots": "4", "startSet": "4", "item1": "4"},
        {"cid": "5", "n": "6"},
        {},
        "\x12" + "2",
        ["7"],
        89, 4915, ["8", "9"], "4_14", 53.6,
    ]
    import json
    html = '<script type="qwik/json">' + json.dumps({"objs": objs}) + "</script>"
    b = _qwik_build(html)
    assert b["header"] == {"cid": 89, "n": 4915}
    assert b["runes"] == {} and b["spells"] == [["4_14", 53.6]]
    assert _qwik_build("<html></html>") is None
