import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import views
from config import Config
from controller import Controller
from data.builds import Build, Option
from data.ddragon import Assets, Item
from data.live import parse
from engine import BuildEvaluation
from state import Stage

LIVE = json.loads((Path(__file__).parent / "fixtures" / "live.json").read_text())


def test_parse_live_game():
    g = parse(LIVE)
    assert g.champion == "Taric" and g.role == "support" and g.level == 9
    assert g.items == [3869, 3047, 2055]  # slot order
    assert g.enemies["bottom"] == "Aphelios" and g.enemies["support"] == "Janna"
    assert "support" not in g.allies  # that's you
    assert parse({}) is None


class Champs:
    def name(self, c): return f"c{c}"
    def icon_url(self, c): return ""


def assets():
    a = Assets("1", {}, [], {})
    for iid, gold, tags in [(3865, 400, []), (3869, 400, []), (3047, 1200, ["Boots"]), (3111, 1250, ["Boots"]),
                            (3190, 2200, []), (3109, 2300, [])]:
        a.items[iid] = Item(iid, f"i{iid}", "", gold, tags, {}, [], "")
    return a


def test_ingame_ticks_owned_items_and_next_cost():
    b = Build(44, "support", 51.0, 10000, {}, {}, {
        "core": [Option("3865_3047_3190", 51, 10, 1000)],
        "boots": [Option("3111", 52, 30, 3000)],
        "item3": [Option("3190", 55, 8, 800)],
        "item4": [Option("3109", 60, 5, 500)],
    })
    g = parse(LIVE)  # holds 3869 (upgraded support item), 3047 (boots), 1320 gold
    out = views.ingame(views.header("support", "master_plus", 14), g, BuildEvaluation(44, "support", b, {}), Champs(), assets())
    assert [p["owned"] for p in out["core"]] == [True, True, False]
    assert out["next"]["id"] == 3190 and out["next"]["gold"] == 2200
    assert [s["title"] for s in out["sections"]] == ["3rd item", "4th item"]  # boots hidden: you have boots


def test_ingame_panel_only_when_toggled():
    pushed = []
    ctl = Controller(Config(), pushed.append)
    ctl.session.phase = "InProgress"
    assert ctl.session.stage == Stage.INGAME
    ctl.render()
    assert pushed[-1]["view"] == "status"
    ctl.ingame_shown = True
    ctl.render()
    assert pushed[-1]["message"] == "Waiting for game data..."
