"""Panel payloads. Every payload has a `view` the panel routes on; builders here are pure."""
from __future__ import annotations

from data import builds
from data.builds import Option
from data.champions import Champions
from data.ddragon import Assets
from data.live import LiveGame
from engine import BanEvaluation, BuildEvaluation, Evaluation
from scoring import LANES, DraftOdds, OddsTerm
from state import Stage

TIERS = {"master_plus": "Master+", "grandmaster_plus": "GM+", "challenger": "Challenger"}
LOW_DATA = 0.25  # below this share of possible matchups found, warn that the tier is thin
FLEX = 10.0  # an enemy whose second most played lane is at least this % is marked as a flex pick
BAN_MESSAGE = "Ban phase - recommendations appear after the first pick."
FIRST_PICK_MESSAGE = "Waiting for the first pick..."
STAGE_MESSAGES = {
    Stage.IDLE: "Waiting for champ select...",
    Stage.LOBBY: "In lobby - waiting for you to queue...",
    Stage.QUEUE: "In queue - waiting for a match...",
    Stage.READY: "Match found!",
    Stage.LOADING: "Game starting...",
    Stage.INGAME: "In game",
    Stage.POSTGAME: "Game over",
}


def header(role: str | None, tier: str, days: int) -> dict:
    return {
        "role": role or "", "tier": TIERS.get(tier, tier), "tier_id": tier, "days": days,
        "tiers": [{"id": k, "label": v} for k, v in TIERS.items()],
    }


def status(head: dict, message: str, kind: str = "waiting") -> dict:
    return head | {"view": "status", "status": kind, "message": message}


def picks(head: dict, ev: Evaluation, champs: Champions, enemy_lanes: dict[int, str]) -> dict:
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    d = ev.draft
    found = sum(1 for r in ev.recs for c in r.contributions if c.label == "vs")
    possible = len(ev.recs) * len(d.enemies)
    note = ""
    if possible and found / possible < LOW_DATA:
        note = "Few matchups at this tier: scores lean on overall win rate."

    def enemy(cid: int) -> dict:
        rates = ev.enemy_rates.get(cid, {})
        ranked = sorted(rates.values(), reverse=True)
        return champ(cid) | {
            "id": cid, "lane": ev.lanes.get(cid, ""), "manual": cid in enemy_lanes,
            "flex": len(ranked) > 1 and ranked[1] >= FLEX,
            "rates": {l: round(rates.get(l, 0)) for l in LANES},
        }

    return head | {
        "view": "picks",
        "status": "active",
        "message": "",
        "note": note,
        "locked": champ(d.my_locked) if d.my_locked else None,
        "allies": [
            champ(s.champion_id) | {"lane": s.position or ev.lanes.get(s.champion_id, ""),
                                    "tentative": s.tentative, "is_me": s.is_me}
            for s in d.allies if s.champion_id
        ],
        "enemies": [enemy(c) for c in d.enemies],
        "bans": [champ(c) for c in sorted(d.bans)],
        "recs": [
            champ(r.champion_id) | {
                "id": r.champion_id, "score": round(r.score, 2), "base": round(r.base, 2),
                "contributions": [
                    champ(c.champion_id) | {
                        "kind": c.label, "lane": c.lane, "delta": round(c.delta, 2),
                        "games": c.games, "weighted": round(c.weighted, 2), "tentative": c.tentative,
                    }
                    for c in sorted(r.contributions, key=lambda c: abs(c.weighted), reverse=True)
                ],
            }
            for r in ev.recs
        ],
    }


def bans(head: dict, bev: BanEvaluation, champs: Champions) -> dict:
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    d = bev.draft
    return head | {
        "view": "bans",
        "status": "active",
        "message": "",
        "allies": [
            champ(s.champion_id) | {"lane": s.position, "tentative": s.tentative, "is_me": s.is_me}
            for s in d.allies if s.champion_id
        ],
        "bans": [champ(c) for c in sorted(d.bans)],
        "picks": [champ(c) for c in bev.picks],
        "threats": [
            champ(t.champion_id) | {
                "id": t.champion_id, "lane": t.lane, "score": round(t.score, 3),
                "pick_rate": round(t.pick_rate, 1), "strength": round(t.strength, 2),
                "against": [
                    champ(c.champion_id) | {"delta": round(c.delta, 2), "games": c.games, "weighted": round(c.weighted, 2)}
                    for c in sorted(t.against, key=lambda c: c.weighted)
                ],
            }
            for t in bev.threats
        ],
    }


SKILLS = {"1": "Q", "2": "W", "3": "E", "4": "R"}
SUPPORT_ITEMS = {3865, 3866, 3867, 3869, 3870, 3871, 3876, 3877}  # World Atlas line
ITEM_SECTIONS = [("start", "Start"), ("core", "Core"), ("boots", "Boots"), ("item1", "Support item"),
                 ("item2", "2nd item"), ("item3", "3rd item"), ("item4", "4th item"), ("item5", "5th item")]


def build(head: dict, bev: BuildEvaluation, champs: Champions, assets: Assets, top: int = 4) -> dict:
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    b = bev.build
    out = head | {"view": "build", "status": "active", "message": "", "champ": champ(bev.champion_id)}
    if b is None:
        return out | {"empty": "No build data for this champion and role at this tier."}

    def vs(o: Option) -> dict:
        return {"matchup": round(o.matchup, 2),
                "vs": [champ(v.enemy) | {"lane": v.lane, "delta": round(v.delta, 2), "games": v.games} for v in o.vs]}

    def option(o: Option, look, best: Option | None) -> dict:
        return {"parts": [look(i) for i in o.ids], "wr": round(o.wr, 1), "pr": round(o.pr, 1), "games": o.games,
                "best": best is not None and o.key == best.key} | vs(o)

    def rune(key: str) -> dict:
        o = b.runes.get(key)
        info = assets.rune(int(key.rstrip("f")))
        return info | ({"wr": round(o.wr, 1), "pr": round(o.pr, 1), "games": o.games} | vs(o) if o else {})

    pages = []
    for kind, label in (("pick", "Most picked"), ("win", "Highest win rate")):
        pg = b.pages.get(kind)
        if not pg or (kind == "win" and pages and pg.primary == b.pages["pick"].primary and pg.secondary == b.pages["pick"].secondary):
            continue
        styles = [assets.runes[r].style for r in pg.primary[:1] + pg.secondary[:1] if r in assets.runes]
        pages.append({
            "label": label, "wr": round(pg.wr, 1), "games": pg.games, "small": pg.games < 300,
            "styles": [assets.rune(st) for st in styles],
            "primary": [rune(str(r)) for r in pg.primary], "secondary": [rune(str(r)) for r in pg.secondary],
            "shards": [rune(k) for k in builds.shard_keys(pg.shards)],
        })
    keystones = sorted(
        (o for k, o in b.runes.items() if k.isdigit() and (r := assets.runes.get(int(k))) and r.slot == 0 and o.pr >= 1),
        key=lambda o: -o.pr,
    )
    sections = []
    for key, title in ITEM_SECTIONS:
        opts = b.sections.get(key) or []
        if key == "item1" and not any(i in SUPPORT_ITEMS for o in opts[:1] for i in o.ids):
            title = "1st item"
        best = builds.best_vs(opts)
        shown = opts[:top] + ([best] if best and best not in opts[:top] else [])
        if shown:
            sections.append({"key": key, "title": title, "options": [option(o, assets.item, best) for o in shown]})
    spells = b.sections.get("spells") or []
    best_spell = builds.best_vs(spells)
    order = (b.sections.get("skill_order") or [None])[0]
    return out | {
        "games": b.games, "wr": round(b.wr, 1), "lane": bev.role,
        "opponents": [champ(e) | {"lane": l, "games": g, "wr": round(w, 1)} for e, l, g, w in b.vs],
        "pages": pages,
        "keystones": [rune(o.key) for o in keystones[:3]],
        "spells": [option(o, assets.summoner, best_spell) for o in spells[:3]],
        "skills": [{"key": o.key, "wr": round(o.wr, 1), "pr": round(o.pr, 1), "games": o.games} for o in (b.sections.get("skills") or [])[:2]],
        "skill_order": "".join(SKILLS.get(ch, "?") for ch in order.key) if order else "",
        "items": sections,
    }


def _owned(iid: int, items: set[int], assets: Assets) -> bool:
    """You have this item, or (support item, boots) anything further along its line."""
    if iid in items:
        return True
    if iid in SUPPORT_ITEMS:
        return bool(items & SUPPORT_ITEMS)
    it = assets.items.get(iid)
    if it and "Boots" in it.tags:
        return any("Boots" in (o.tags if (o := assets.items.get(i)) else []) and i != 1001 for i in items)
    return False


def ingame(head: dict, live: LiveGame, bev: BuildEvaluation | None, champs: Champions, assets: Assets) -> dict:
    """In-game: the build worked out before the game (your champion vs the lanes), with what you
    already own. Uses only your own items and gold; never the enemies' items."""
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    out = head | {"view": "ingame", "status": "active", "message": "", "level": live.level,
                  "gold": int(live.gold), "time": int(live.game_time)}
    b = bev.build if bev else None
    if bev:
        out["champ"] = champ(bev.champion_id)
    if b is None:
        return out | {"empty": "No build data for this champion and role."}
    owned = set(live.items)

    def item(iid: int) -> dict:
        it = assets.items.get(iid)
        return assets.item(iid) | {"gold": it.gold if it else 0, "owned": _owned(iid, owned, assets)}

    def opt(o: Option) -> dict:
        return {"parts": [item(i) for i in o.ids], "wr": round(o.wr, 1), "pr": round(o.pr, 1), "games": o.games,
                "matchup": round(o.matchup, 2), "has_vs": bool(o.vs)}

    core = (b.sections.get("core") or [None])[0]
    path = [item(i) for i in core.ids] if core else []
    nxt = next((p for p in path if not p["owned"]), None)
    sections = []
    for key, title in (("boots", "Boots"), ("item3", "3rd item"), ("item4", "4th item"), ("item5", "5th item")):
        opts = b.sections.get(key) or []
        if opts and all(_owned(i, owned, assets) for i in opts[0].ids):
            continue  # the top option is already bought (_owned also matches another boot/support tier)
        if opts:
            sections.append({"title": title, "options": [opt(o) for o in opts[:3]]})
    order = (b.sections.get("skill_order") or [None])[0]
    page = b.pages.get("pick")
    return out | {
        "opponents": [champ(e) | {"lane": l} for e, l, _, _ in b.vs],
        "core": path,
        "next": nxt,
        "sections": sections,
        "skill_order": "".join(SKILLS.get(ch, "?") for ch in order.key) if order else "",
        "keystone": assets.rune(page.primary[0]) if page and page.primary else None,
    }


def odds(o: DraftOdds | None, champs: Champions, top: int = 4) -> dict | None:
    if o is None:
        return None
    name = champs.name
    def label(t: OddsTerm) -> str:
        if t.kind == "strength":
            return f"{name(t.champions[0])} ({t.lanes[0]})"
        joiner = " vs " if t.kind == "matchup" else " + "
        return joiner.join(name(c) for c in t.champions)
    return {
        "win": round(o.win, 1), "known": o.known, "pending": o.pending,
        "reasons": [{"kind": t.kind, "label": label(t), "value": round(t.value, 2),
                     "icons": [champs.icon_url(c) for c in t.champions]} for t in o.terms[:top] if abs(t.value) >= 0.1],
    }


def record(ev: Evaluation, o: DraftOdds | None = None) -> dict:
    """Compact form of an evaluation for the store (backtesting)."""
    return {
        "odds": round(o.win, 2) if o else None,
        "role": ev.role,
        "lanes": {str(c): l for c, l in ev.lanes.items()},
        "allies": [[s.champion_id, s.position, s.tentative] for s in ev.draft.allies if s.champion_id],
        "enemies": ev.draft.enemies,
        "recs": [
            {"id": r.champion_id, "score": r.score, "base": r.base,
             "c": [[c.label, c.champion_id, c.lane, c.delta, c.games, c.weighted] for c in r.contributions]}
            for r in ev.recs
        ],
    }
