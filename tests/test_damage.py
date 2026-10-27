import pytest

import views
from data.champions import Champions
from engine import Evaluation
from lcu import DraftState

DATA = {"data": {
    n: {"key": str(k), "id": n, "name": n, "image": {"full": f"{n}.png"}, "info": {"attack": a, "magic": m}}
    for n, k, a, m in [("Zed", 1, 9, 1), ("Annie", 2, 2, 10), ("Amumu", 3, 2, 6), ("Blank", 4, 0, 0)]
}}
champs = Champions(DATA, "1")


def test_damage_from_ddragon_ratings():
    assert champs.damage(1) == pytest.approx((0.9, 0.1))
    assert champs.damage(4) == (0.5, 0.5)


def test_damage_override_beats_ratings():
    assert champs.damage(3)[1] == pytest.approx(0.85)


def test_team_damage_ignores_unknown_and_empty():
    assert champs.team_damage([0, 99]) is None
    assert champs.team_damage([1, 0, 2])[0] == pytest.approx((0.9 + 2 / 12) / 2)


def picks(enemies):
    d = DraftState(enemies=enemies)
    return views.picks(views.header("mid", "master_plus", 14), Evaluation(d, "mid", [], {}), champs, {})


def test_picks_includes_enemy_damage():
    out = picks([1, 2])
    assert out["enemy_damage"]["ad"] + out["enemy_damage"]["ap"] == 100
    assert out["enemy_damage"]["ap"] == round((0.1 + 10 / 12) / 2 * 100)


def test_picks_omits_enemy_damage_without_enemies():
    assert "enemy_damage" not in picks([])
