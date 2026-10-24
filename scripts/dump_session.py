import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.champions import Champions
from config import load_config
from lcu import LcuClient, LcuError, parse_session, read_lockfile


def main() -> int:
    try:
        client = LcuClient(read_lockfile(load_config().lockfile))
        session = client.get_session()
    except LcuError as e:
        print(e)
        return 1
    except Exception as e:
        print(f"client not running or unreachable: {e}")
        return 1
    if session is None:
        print("not in champ select")
        return 0
    print(json.dumps(session, indent=2))
    try:
        name = Champions.load().name
    except Exception:
        name = lambda i: f"#{i}"
    d = parse_session(session)
    print(f"\nme: cell {d.my_cell_id} position {d.my_position or '(blind)'}")
    for s in d.allies:
        who = name(s.champion_id) if s.champion_id else "-"
        print(f"  ally {s.position or '-':8} {who}{' (hover)' if s.tentative else ''}{' <- me' if s.is_me else ''}")
    print("enemies:", [name(c) for c in d.enemies])
    print("bans:", sorted(name(c) for c in d.bans))
    print("picked:", sorted(name(c) for c in d.picked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
