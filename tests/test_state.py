import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from controller import Controller
from data.store import Store
from engine import Evaluation
from scoring import Recommendation
from state import Event, Session, Stage, in_ban_phase

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "session.json").read_text())


def session(ban_in_progress=False, my_pick=0):
    s = copy.deepcopy(FIXTURE)
    s["actions"][0][2]["isInProgress"] = ban_in_progress
    if my_pick:
        s["actions"][1][2].update(completed=True, championId=my_pick)  # cell 2 is the local player
    return s


def test_stage_follows_gameflow_before_draft():
    s = Session()
    assert s.stage == Stage.IDLE
    for phase, stage in [("Lobby", Stage.LOBBY), ("Matchmaking", Stage.QUEUE), ("ReadyCheck", Stage.READY)]:
        assert s.on_phase(phase) == [Event.PHASE]
        assert s.stage == stage
    assert s.on_phase("ReadyCheck") == []  # repeats are not events


def test_draft_ban_pick_lock_and_end():
    s = Session()
    s.on_phase("ChampSelect")
    events = s.on_session(session(ban_in_progress=True))
    assert events == [Event.DRAFT_STARTED, Event.DRAFT_CHANGED]
    assert s.stage == Stage.BAN
    assert s.on_session(session()) == [Event.DRAFT_CHANGED]
    assert s.stage == Stage.PICK
    assert s.on_session(session(my_pick=412)) == [Event.LOCKED_IN, Event.DRAFT_CHANGED]
    assert s.stage == Stage.LOCKED
    assert 412 not in s.frozen.picked  # your own champion stays rankable in the frozen snapshot
    assert s.on_session(session(my_pick=412)) == [Event.DRAFT_CHANGED]  # locked in only once
    events = s.on_phase("InProgress")
    assert events == [Event.PHASE, Event.DRAFT_ENDED]
    assert s.stage == Stage.INGAME and s.draft is None and s.frozen is None


def test_enemy_lane_overrides_reset_each_draft():
    s = Session()
    s.on_session(session())
    s.set_enemy_lane(111, "bottom", ("top", "jungle", "middle", "bottom", "support"))
    s.set_enemy_lane(103, "bottom", ("top", "jungle", "middle", "bottom", "support"))
    assert s.enemy_lanes == {103: "bottom"}  # one champion per lane
    s.on_session(None)
    s.on_session(session())
    assert s.enemy_lanes == {}


def test_ban_phase_detection():
    assert in_ban_phase(session(ban_in_progress=True))
    assert not in_ban_phase(session())


class StubChamps:
    def name(self, cid): return f"c{cid}"
    def icon_url(self, cid): return ""


class StubRec:
    tier = "master_plus"
    champs = StubChamps()

    @staticmethod
    def role_for(draft):
        return draft.my_position or None

    def evaluate(self, draft, pickable=None, enemy_lanes=None):
        return Evaluation(draft, draft.my_position, [Recommendation(412, 2.5, 1.0, [])], {})

    def odds_inputs(self, ev):
        return {}, {}


def test_controller_records_draft(tmp_path):
    pushed = []
    store = Store(tmp_path / "s.sqlite")
    ctl = Controller(Config(), pushed.append, store)
    ctl.rec = StubRec()
    ctl.on_phase("ChampSelect")
    ctl.on_session(session())
    ctl.on_session(session())  # identical snapshot is skipped
    assert pushed[-1]["view"] == "picks" and pushed[-1]["role"] == "support"
    ctl.on_session(session(my_pick=412))
    ctl.on_phase("InProgress")
    assert pushed[-1]["view"] == "status"
    (draft,) = store.drafts()
    assert draft["role"] == "support" and draft["my_champ"] == 412 and draft["ended"]
    assert len(store.draft_snapshots(draft["id"])) == 2
    assert store.draft_recs(draft["id"], "locked")["recs"][0]["id"] == 412
    assert store.draft_recs(draft["id"], "final") is not None


def test_controller_status_views():
    pushed = []
    ctl = Controller(Config(), pushed.append)
    ctl.on_phase("Matchmaking")
    assert pushed[-1]["view"] == "status" and "queue" in pushed[-1]["message"].lower()


class StubBanRec(StubRec):
    def bans(self, draft, pickable=None):
        from engine import BanEvaluation
        from scoring import Threat
        return BanEvaluation(draft, draft.my_position, [412], [Threat(555, "bottom", 0.3, 10.0, 1.0, [])])


def test_ban_phase_shows_no_recommendations_by_default():
    pushed = []
    ctl = Controller(Config(), pushed.append)
    ctl.rec = StubBanRec()
    ctl.on_phase("ChampSelect")
    ctl.on_session(session(ban_in_progress=True))
    assert pushed[-1]["view"] == "status" and "recs" not in pushed[-1] and "threats" not in pushed[-1]
    ctl.on_session(session())  # picks start
    assert pushed[-1]["view"] == "picks"


def test_ban_phase_shows_bans_then_your_tab_choice():
    pushed = []
    ctl = Controller(Config(show_ban_suggestions=True), pushed.append)
    ctl.rec = StubBanRec()
    ctl.on_phase("ChampSelect")
    ctl.on_session(session(ban_in_progress=True))
    assert pushed[-1]["view"] == "bans" and pushed[-1]["tabs"] == ["bans", "picks"]
    assert pushed[-1]["threats"][0]["id"] == 555
    ctl.set_tab("picks")
    assert pushed[-1]["view"] == "picks" and pushed[-1]["tab"] == "picks"
    ctl.on_session(session())  # ban phase over: picks, no tabs
    assert pushed[-1]["view"] == "picks" and "tabs" not in pushed[-1]


def test_preload_runs_once_per_role(monkeypatch):
    ctl = Controller(Config(), lambda s: None)
    loaded = []

    class Rec(StubRec):
        def has_pool(self, role, tier=None): return False
        def pool(self, role, progress=None, tier=None): loaded.append(role); progress("Loading x 1/1...")
    ctl.rec = Rec()
    ctl.lobby_roles = lambda: ["support", "bottom"]
    started = []
    monkeypatch.setattr("controller.threading.Thread", lambda target, args, **kw: started.append((target, args)) or type("T", (), {"start": lambda self: target(*args)})())
    ctl.on_phase("Lobby")
    ctl.on_phase("Matchmaking")  # same roles: nothing new to load
    assert loaded == ["support", "bottom"] and len(started) == 1
    assert ctl.preload == "Stats ready: support, bottom"


def no_picks_yet(my_turn=False):
    s = session()
    for a in s["actions"][1]:
        a.update(completed=False, championId=0, isInProgress=False)
    s["actions"][1][2]["isInProgress"] = my_turn  # cell 2 is you
    for p in s["myTeam"] + s["theirTeam"]:
        p["championId"] = 0
    return s


def test_recommendations_wait_for_first_pick_unless_its_your_turn():
    pushed = []
    ctl = Controller(Config(), pushed.append)
    ctl.rec = StubRec()
    ctl.on_phase("ChampSelect")
    ctl.on_session(no_picks_yet())
    assert pushed[-1]["view"] == "status" and "first pick" in pushed[-1]["message"]
    ctl.on_session(no_picks_yet(my_turn=True))
    assert pushed[-1]["view"] == "picks"
    ctl.on_session(session())  # someone has locked in
    assert pushed[-1]["view"] == "picks"
