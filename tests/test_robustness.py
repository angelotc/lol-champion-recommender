"""Failure handling: offline fallbacks, retries, and the LCU watcher surviving bad events."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pytest

import lcu
from data import fetch as fetch_mod
from data.champions import Champions
from data.fetch import Fetcher
from data.lolalytics import Lolalytics


def test_offline_champions_ignore_other_ddragon_files(tmp_path, monkeypatch):
    champs = {"data": {"Annie": {"key": "1", "id": "Annie", "name": "Annie", "tags": []}}}
    (tmp_path / "ddragon_16.1.1.json").write_text(json.dumps(champs), encoding="utf-8")
    items = tmp_path / "ddragon_items_16.1.1.json"
    items.write_text(json.dumps({"data": {"1001": {}}}), encoding="utf-8")
    later = time.time() + 10
    os.utime(items, (later, later))  # newest file, but not champion data

    def offline(_):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(Champions, "_fetch", classmethod(lambda cls, d: offline(d)))
    c = Champions.load(tmp_path)
    assert c.version == "16.1.1"
    assert c.name(1) == "Annie"


def fetcher_with(handler) -> Fetcher:
    f = Fetcher(per_second=1000)
    f.http = httpx.Client(transport=httpx.MockTransport(handler))
    return f


def test_fetch_retries_rate_limits(monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429 if len(calls) < 3 else 200, json={"ok": 1})

    r = fetcher_with(handler).get("https://x.test/")
    assert r.status_code == 200 and len(calls) == 3


def test_fetch_retries_dropped_connections(monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200)

    assert fetcher_with(handler).get("https://x.test/").status_code == 200
    assert len(calls) == 2


def test_lolalytics_server_errors_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    body = {"status": 500}
    f = fetcher_with(lambda req: httpx.Response(200, json=body))
    lola = Lolalytics(fetcher=f, cache_dir=tmp_path)
    assert lola.counters("annie", "middle", "top") == {}
    assert f.failures == 1
    assert not list(tmp_path.glob("*.json"))  # a later call will try again

    body.clear()
    body["status"] = 404  # unknown champion: genuinely empty, fine to cache
    assert lola.counters("zzz", "middle", "top") == {}
    assert f.failures == 1


def test_watcher_survives_callback_errors(monkeypatch):
    """An error handling one event must not stop the watcher; it reconnects."""
    connects = []

    class FakeWs:
        def __init__(self):
            self.msgs = [json.dumps([8, lcu.SESSION_EVENT, {"eventType": "Update", "data": {"n": len(connects)}}])]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, _):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.msgs:
                return self.msgs.pop()
            raise StopAsyncIteration

    def connect(*a, **k):
        connects.append(1)
        return FakeWs()

    class FakeClient:
        def __init__(self, c):
            pass

        def get_session(self):
            raise ValueError("bad json")  # not an httpx error

        def close(self):
            pass

    async def no_sleep(_):
        pass

    monkeypatch.setattr(lcu.websockets, "connect", connect)
    monkeypatch.setattr(lcu, "LcuClient", FakeClient)
    monkeypatch.setattr(lcu.asyncio, "sleep", no_sleep)

    seen = []

    def callback(data):
        seen.append(data)
        if len(connects) >= 3:
            raise asyncio.CancelledError  # done: stop the test loop
        raise RuntimeError("boom")

    creds = lcu.LcuCreds(port=1, password="x") if hasattr(lcu, "LcuCreds") else None
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lcu.watch_session(creds, callback))
    assert len(connects) >= 3
