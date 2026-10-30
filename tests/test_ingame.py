import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draft import views
from config import Config
from draft.controller import Controller
from data.builds import Build, Option
from data.ddragon import Assets, Item
from data.live import parse
from draft.engine import BuildEvaluation
from draft.state import Stage

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
        "item5": [Option("3865", 58, 4, 400)],  # support item: you hold one, section hidden
    })
    g = parse(LIVE)  # holds 3869 (upgraded support item), 3047 (boots), 1320 gold
    out = views.ingame(views.header("support", "master_plus", 14), g, BuildEvaluation(44, "support", b, {}), Champs(), assets())
    assert [p["owned"] for p in out["core"]] == [True, True, False]
    assert out["next"]["id"] == 3190 and out["next"]["gold"] == 2200
    # boots stay with their matchup delta; 5th hidden (you hold the support item line)
    assert [s["title"] for s in out["sections"]] == ["Boots", "3rd item", "4th item"]
    boots = out["sections"][0]["options"]
    assert [o["parts"][0]["id"] for o in boots] == [3111] and "matchup" in boots[0]
    assert not boots[0]["yours"] and not boots[0]["parts"][0]["owned"]  # you hold 3047, not 3111


def test_ingame_boots_hidden_once_you_hold_a_pair():
    b = Build(44, "support", 51.0, 10000, {}, {}, {
        "boots": [Option(str(i), 50, 10, 1000) for i in (3111, 3190, 3109, 3865)] + [Option("3047", 49, 2, 100)],
    })
    out = views.ingame(views.header("support", "master_plus", 14), parse(LIVE), BuildEvaluation(44, "support", b, {}), Champs(), assets())
    assert out["sections"] == []  # you hold 3047, one of the options


def test_ingame_core_follows_what_you_bought():
    # most picked core goes Zeke's (3050); you bought Locket (3190) instead
    b = Build(12, "support", 51.0, 10000, {}, {}, {
        "core": [Option("3869_3111_3050", 56, 15, 5000), Option("3869_3111_3190", 55, 10, 3000)],
        "item3": [Option("3050", 57, 15, 1500), Option("3190", 58, 14, 1400), Option("3109", 60, 25, 2500)],
    })
    g = parse(LIVE)
    g.items = [3869, 3047, 3190]
    out = views.ingame(views.header("support", "master_plus", 14), g, BuildEvaluation(12, "support", b, {}), Champs(), assets())
    assert [p["id"] for p in out["core"]] == [3869, 3111, 3190]
    assert out["next"] is None
    third = next(s for s in out["sections"] if s["title"] == "3rd item")
    assert [o["parts"][0]["id"] for o in third["options"]] == [3050, 3109]  # Locket dropped: you own it
    assert third["state"] == "next"  # core done: the 3rd slot is what you buy next


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


def test_ingame_slot_follows_items_bought_not_top_option():
    # Knight's Vow (3109) bought 3rd also tops the 4th-item list; the 4th slot must still show
    b = Build(44, "support", 51.0, 10000, {}, {}, {
        "core": [Option("3869_3047_3190", 51, 10, 1000)],
        "item3": [Option("3050", 57, 15, 1500), Option("3109", 58, 14, 1400)],
        "item4": [Option("3109", 62, 20, 400), Option("3050", 61, 19, 390)],
        "item5": [Option("3050", 60, 10, 100)],
    })
    g = parse(LIVE)
    g.items = [3869, 3047, 3190, 3109, 3070]  # 3070: a component, not a finished item
    a = assets()
    a.items[3050] = Item(3050, "i3050", "", 2200, [], {}, [], "")
    a.items[3070] = Item(3070, "i3070", "", 400, [], {}, [3004], "")
    out = views.ingame(views.header("support", "master_plus", 14), g, BuildEvaluation(44, "support", b, {}), Champs(), a)
    # the filled 3rd slot is hidden; the 4th is next
    assert [(x["title"], x["state"]) for x in out["sections"]] == [("4th item", "next"), ("5th item", "")]
    assert [o["parts"][0]["id"] for o in out["sections"][0]["options"]] == [3050]  # Knight's Vow dropped: you own it


def test_ingame_no_item_slot_is_next_while_buying_the_core():
    b = Build(44, "support", 51.0, 10000, {}, {}, {
        "core": [Option("3869_3047_3190", 51, 10, 1000)],
        "item3": [Option("3109", 58, 14, 1400)],
    })
    out = views.ingame(views.header("support", "master_plus", 14), parse(LIVE), BuildEvaluation(44, "support", b, {}), Champs(), assets())
    assert out["next"]["id"] == 3190  # the core's next item is the highlight instead
    assert [x["state"] for x in out["sections"] if x["title"] == "3rd item"] == [""]


def test_ingame_filled_slot_hidden_even_when_your_item_ranks_low():
    b = Build(44, "support", 51.0, 10000, {}, {}, {
        "core": [Option("3869_3047_3190", 51, 10, 1000)],
        "item3": [Option(str(i), 55, 10, 1000) for i in (3050, 3107, 3222, 3109)],
        "item4": [Option("3050", 55, 10, 1000)],
    })
    g = parse(LIVE)
    g.items = [3869, 3047, 3190, 3109]
    a = assets()
    for iid in (3050, 3107, 3222):
        a.items[iid] = Item(iid, f"i{iid}", "", 2200, [], {}, [], "")
    out = views.ingame(views.header("support", "master_plus", 14), g, BuildEvaluation(44, "support", b, {}), Champs(), a)
    assert [x["title"] for x in out["sections"]] == ["4th item"]


def test_damage_split_sums_enemy_team():
    bev = BuildEvaluation(44, "support", None, {"bottom": 1, "middle": 2},
                          {1: {"physical": 9000, "magic": 0, "true": 1000}, 2: {"physical": 0, "magic": 10000, "true": 0}})
    d = views.damage_split(bev, Champs())
    assert (d["physical"], d["magic"], d["true"]) == (45, 50, 5)
    assert [(e["name"], e["lane"], e["physical"]) for e in d["enemies"]] == [("c1", "bottom", 90), ("c2", "middle", 0)]
    assert views.damage_split(BuildEvaluation(44, "support", None, {}), Champs()) is None
