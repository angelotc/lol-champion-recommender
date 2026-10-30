import json

from data.live import GameRecorder


def test_snapshots_append_then_new_file_per_game(tmp_path):
    rec = GameRecorder(tmp_path / "recordings", every=15)
    assert rec.due()
    rec.save({"gameData": {"gameTime": 10}})
    assert not rec.due()
    rec.last = 0  # pretend the interval passed
    rec.save({"gameData": {"gameTime": 25}})
    first = rec.path
    lines = [json.loads(l) for l in first.read_text(encoding="utf-8").splitlines()]
    assert [l["data"]["gameData"]["gameTime"] for l in lines] == [10, 25]

    rec.end()
    assert rec.path is None and rec.due()
