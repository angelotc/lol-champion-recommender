"""Data Dragon names and icons for items, runes and summoner spells (champions: data/champions.py)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from paths import CACHE_DIR

DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON_PERKS = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods"
log = logging.getLogger(__name__)
# stat shards aren't in runesReforged.json
SHARDS = {
    5001: ("Health (scaling)", "statmodshealthplusicon.png"),
    5002: ("Armor", "statmodsarmoricon.png"),
    5003: ("Magic Resist", "statmodsmagicresicon.png"),
    5005: ("Attack Speed", "statmodsattackspeedicon.png"),
    5007: ("Ability Haste", "statmodscdrscalingicon.png"),
    5008: ("Adaptive Force", "statmodsadaptiveforceicon.png"),
    5010: ("Move Speed", "statmodsmovementspeedicon.png"),
    5011: ("Health", "statmodshealthscalingicon.png"),
    5013: ("Tenacity and Slow Resist", "statmodstenacityicon.png"),
}


@dataclass
class Rune:
    id: int
    name: str
    icon: str
    style: int        # tree id (8000 Precision, 8100 Domination, ...) ; 0 for shards
    slot: int         # 0 = keystone row, 1-3 = minor rows; -1 for shards


@dataclass
class Item:
    id: int
    name: str
    icon: str
    gold: int
    tags: list[str]
    stats: dict[str, float]
    into: list[int]
    plaintext: str


class Assets:
    def __init__(self, version: str, items: dict, runes: list, summoners: dict):
        self.version = version
        self.items: dict[int, Item] = {}
        for k, v in (items.get("data") or {}).items():
            self.items[int(k)] = Item(
                int(k), v.get("name", k), f"{DDRAGON}/cdn/{version}/img/item/{k}.png",
                int((v.get("gold") or {}).get("total") or 0), list(v.get("tags") or []),
                dict(v.get("stats") or {}), [int(i) for i in v.get("into") or []], v.get("plaintext", ""),
            )
        self.runes: dict[int, Rune] = {}
        self.styles: dict[int, Rune] = {}
        for style in runes or []:
            sid = int(style["id"])
            self.styles[sid] = Rune(sid, style["name"], f"{DDRAGON}/cdn/img/{style['icon']}", sid, -2)
            for i, slot in enumerate(style.get("slots") or []):
                for r in slot.get("runes") or []:
                    self.runes[int(r["id"])] = Rune(int(r["id"]), r["name"], f"{DDRAGON}/cdn/img/{r['icon']}", sid, i)
        for sid, (name, icon) in SHARDS.items():
            self.runes[sid] = Rune(sid, name, f"{CDRAGON_PERKS}/{icon}", 0, -1)
        self.summoners: dict[int, tuple[str, str]] = {
            int(v["key"]): (v["name"], f"{DDRAGON}/cdn/{version}/img/spell/{v['image']['full']}")
            for v in (summoners.get("data") or {}).values()
        }

    @classmethod
    def load(cls, version: str, cache_dir: Path = CACHE_DIR) -> "Assets":
        cache_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "items": f"{DDRAGON}/cdn/{version}/data/en_US/item.json",
            "runes": f"{DDRAGON}/cdn/{version}/data/en_US/runesReforged.json",
            "summoners": f"{DDRAGON}/cdn/{version}/data/en_US/summoner.json",
        }
        data = {}
        with httpx.Client(timeout=15) as c:
            for name, url in files.items():
                path = cache_dir / f"ddragon_{name}_{version}.json"
                if path.exists():
                    data[name] = json.loads(path.read_text(encoding="utf-8"))
                    continue
                try:
                    data[name] = c.get(url).raise_for_status().json()
                    path.write_text(json.dumps(data[name]), encoding="utf-8")
                except httpx.HTTPError:
                    older = sorted(cache_dir.glob(f"ddragon_{name}_*.json"), key=lambda p: p.stat().st_mtime)
                    if not older:
                        log.warning("no %s data from Data Dragon", name)
                        data[name] = {} if name != "runes" else []
                    else:
                        data[name] = json.loads(older[-1].read_text(encoding="utf-8"))
        return cls(version, data["items"], data["runes"], data["summoners"])

    def item(self, iid: int) -> dict:
        it = self.items.get(int(iid))
        return {"id": int(iid), "name": it.name if it else f"#{iid}", "icon": it.icon if it else ""}

    def rune(self, rid: int) -> dict:
        r = self.runes.get(int(rid)) or self.styles.get(int(rid))
        return {"id": int(rid), "name": r.name if r else f"#{rid}", "icon": r.icon if r else ""}

    def summoner(self, sid: int) -> dict:
        name, icon = self.summoners.get(int(sid), (f"#{sid}", ""))
        return {"id": int(sid), "name": name, "icon": icon}
