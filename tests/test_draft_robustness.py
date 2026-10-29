"""Draft-time robustness: hovers, failed stats loads, tier clicks, bad config, LCU callback errors."""
import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from draft import controller as controller_mod
from data import lcu
from config import Config, load_config
from draft.controller import Controller
from draft.engine import Recommender
from data.lcu import DraftState, Slot
from draft.scoring import ChampStats


def stats(cid, wr=50.0):
    return ChampStats(cid, "support", wr, 10000)


def bare_recommender(lola=None) -> Recommender:
    """A Recommender with no network or champion data, for testing its own logic."""
    r = Recommender.__new__(Recommender)
    r.cfg = Config()
    r.lola = lola or SimpleNamespace(tier="master_plus", fetch=SimpleNamespace(failures=0))
    r.pools, r._stats = {}, {}
    r.comfort = lambda role: {}
    r._lane_rates = lambda ids: {}
    return r


def test_teammate_hover_is_not_recommended():
    r = bare_recommender()
    r.pool = lambda role, *a, **k: {1: stats(1, 55), 2: stats(2, 52), 3: stats(3, 51)}
    hover = Slot(1, "middle", True, False)  # a teammate is hovering champion 1
    me = Slot(0, "support", False, True)
    ev = r.evaluate(DraftState(my_position="support", allies=[hover, me]))
    assert [x.champion_id for x in ev.recs] == [2, 3]


def test_own_hover_is_still_recommended():
    r = bare_recommender()
    r.pool = lambda role, *a, **k: {1: stats(1, 55), 2: stats(2, 52)}
    ev = r.evaluate(DraftState(my_position="support", allies=[Slot(1, "support", True, True)]))
    assert ev.recs[0].champion_id == 1


def test_stats_from_a_partly_failed_load_are_not_cached():
    fetch = SimpleNamespace(failures=0)
    calls = []

    def champ_stats(slug, lane):
        calls.append(1)
        if len(calls) == 1:
            fetch.failures += 1  # one of the per-lane requests failed: matchups are incomplete
        return stats(7)

    lola = SimpleNamespace(tier="master_plus", fetch=fetch, champ_stats=champ_stats)
    r = bare_recommender(lola)
    r.champs = SimpleNamespace(slug=lambda cid: "x")
    assert r.lane_stats(7, "support").champion_id == 7  # usable now
    assert r.cached_lane_stats(7, "support") is False  # but not remembered
    r.lane_stats(7, "support")
    assert r.cached_lane_stats(7, "support").champion_id == 7  # a clean load is cached
    assert len(calls) == 2


def test_failed_odds_stats_wait_before_refetching(tmp_path, monkeypatch):
    ctl = Controller(Config(), lambda s: None)
    fetches = []
    done = threading.Event()

    class Rec:
        lola = SimpleNamespace(fetch=SimpleNamespace(map=lambda fn, items: [fn(i) for i in items]))

        def lane_stats(self, cid, lane):
            fetches.append((cid, lane))
            return None  # the request failed

        def cached_lane_stats(self, cid, lane):
            return False

    ctl.rec = Rec()
    monkeypatch.setattr(ctl, "render", lambda: done.set())
    ctl.session = SimpleNamespace(stage=next(iter(controller_mod.DRAFT_STAGES)))
    ctl._load_stats([(1, "top")])
    assert done.wait(5)
    ctl._load_stats([(1, "top")])  # e.g. from the render after the failed load
    assert fetches == [(1, "top")]
    ctl._stats_retry_at[(1, "top")] = 0  # retry window over
    done.clear()
    ctl._load_stats([(1, "top")])
    assert done.wait(5)
    assert len(fetches) == 2


def test_tier_click_before_stats_load_still_answers():
    pushed = []
    ctl = Controller(Config(), pushed.append)
    assert ctl.rec is None
    ctl.set_tier("master_plus")
    assert pushed  # the panel gets a state, so its tier buttons are enabled again


def test_broken_config_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("top_n = [unclosed", encoding="utf-8")
    assert load_config(p) == Config()
    with pytest.raises(Exception):
        load_config(p, strict=True)


def test_callback_error_does_not_end_the_session(monkeypatch):
    """An error handling one event is logged; the watcher doesn't reconnect or report the client gone."""
    connects = []
    msgs = [json.dumps([8, lcu.SESSION_EVENT, {"eventType": "Update", "data": {"n": i}}]) for i in range(3)]

    class FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, _):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if msgs:
                return msgs.pop(0)
            raise asyncio.CancelledError  # all events delivered: stop the test

    def connect(*a, **k):
        connects.append(1)
        return FakeWs()

    class FakeClient:
        def __init__(self, c):
            pass

        def get_session(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(lcu.websockets, "connect", connect)
    monkeypatch.setattr(lcu, "LcuClient", FakeClient)
    seen = []

    def callback(data):
        seen.append(data)
        if data and data["n"] == 0:
            raise RuntimeError("boom")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lcu.watch_session(SimpleNamespace(port=1, password="x"), callback))
    assert connects == [1]
    assert seen == [None, {"n": 0}, {"n": 1}, {"n": 2}]  # no None ("client gone") after the error
