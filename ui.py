from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from champions import Champions
from lcu import DraftState
from scoring import Contribution, Recommendation

ABBR = {"top": "top", "jungle": "jg", "middle": "mid", "bottom": "bot", "support": "sup"}


def _fmt(c: Contribution, champs: Champions) -> str:
    prep = "vs" if c.label == "vs" else "w/"
    q = "?" if c.tentative else ""
    return f"{c.weighted:+.1f} {prep} {champs.name(c.champion_id)}{q} ({ABBR.get(c.lane, c.lane)})"


def _style(v: float) -> str:
    return "green" if v > 0 else "red" if v < 0 else "dim"


def render(draft: DraftState, recs: list[Recommendation], champs: Champions, top_n: int = 8):
    allies = ", ".join(
        f"{champs.name(s.champion_id)}{'?' if s.tentative else ''}"
        + (f" ({ABBR.get(s.position, s.position)})" if s.position else "")
        for s in draft.allies
        if s.champion_id
    ) or "-"
    enemies = ", ".join(champs.name(c) for c in draft.enemies) or "-"
    bans = ", ".join(sorted(champs.name(c) for c in draft.bans)) or "-"
    head = Text()
    head.append(f"Role: {draft.my_position or 'unknown'}\n", style="bold")
    head.append("Allies: ", style="cyan")
    head.append(allies + "\n")
    head.append("Enemies: ", style="red")
    head.append(enemies + "\n")
    head.append("Bans: ", style="yellow")
    head.append(bans)
    table = Table(show_edge=False)
    table.add_column("#", justify="right")
    table.add_column("Champion")
    table.add_column("Score", justify="right")
    table.add_column("Base", justify="right")
    table.add_column("Why")
    for i, r in enumerate(recs[:top_n], 1):
        top = sorted(r.contributions, key=lambda c: abs(c.weighted), reverse=True)[:3]
        why = " | ".join(_fmt(c, champs) for c in top)
        table.add_row(
            str(i),
            champs.name(r.champion_id),
            Text(f"{r.score:+.2f}", style=_style(r.score)),
            Text(f"{r.base:+.2f}", style=_style(r.base)),
            why,
        )
    return Group(head, table)
