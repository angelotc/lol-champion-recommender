"""Durable personal data in one SQLite file: drafts (raw session snapshots and what was
recommended), and your finished games. This is what backtesting and comfort picks learn from."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path

import paths

DB_PATH = paths.DATA_DIR / "store.sqlite"
DRAFT_GAME_WINDOW = 15 * 60  # seconds between a draft ending and its game's creation time
SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    started REAL NOT NULL,
    ended REAL,
    role TEXT,
    tier TEXT,
    my_champ INTEGER,
    game_id INTEGER,
    result TEXT              -- 'win' | 'loss' | NULL (dodged, unknown)
);
CREATE TABLE IF NOT EXISTS snapshots (
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    t REAL NOT NULL,
    session BLOB NOT NULL    -- zlib-compressed JSON of the champ select session
);
CREATE TABLE IF NOT EXISTS recommendations (
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    t REAL NOT NULL,
    kind TEXT NOT NULL,      -- 'locked' | 'final' | 'odds' (one per pick/ban: the timeline)
    payload TEXT NOT NULL    -- JSON: role, tier, lanes, recs with contributions
);
CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    created REAL NOT NULL,
    queue INTEGER,
    champ INTEGER,
    role TEXT,
    win INTEGER,
    duration INTEGER,
    detail TEXT              -- JSON of the full game from the client's match history
);
CREATE INDEX IF NOT EXISTS games_champ ON games(champ, role);
"""


def _pack(obj) -> bytes:
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode())


def unpack(blob: bytes):
    return json.loads(zlib.decompress(blob))


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._last_hash: dict[int, str] = {}

    def _exec(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            return self.db.execute(sql, args)

    # -- drafts -----------------------------------------------------------
    def start_draft(self, role: str | None, tier: str) -> int:
        return self._exec("INSERT INTO drafts(started, role, tier) VALUES (?,?,?)", (time.time(), role, tier)).lastrowid

    def update_draft(self, draft_id: int, **fields) -> None:
        allowed = {"ended", "role", "tier", "my_champ", "game_id", "result"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if cols:
            sets = ", ".join(f"{k}=?" for k in cols)
            self._exec(f"UPDATE drafts SET {sets} WHERE id=?", (*cols.values(), draft_id))

    def snapshot(self, draft_id: int, session: dict) -> None:
        """Store a session snapshot, skipping exact repeats (the client resends unchanged sessions)."""
        blob = _pack(session)
        h = hashlib.sha1(blob).hexdigest()
        if self._last_hash.get(draft_id) == h:
            return
        self._last_hash[draft_id] = h
        self._exec("INSERT INTO snapshots VALUES (?,?,?)", (draft_id, time.time(), blob))

    def save_recs(self, draft_id: int, kind: str, payload: dict) -> None:
        self._exec("INSERT INTO recommendations VALUES (?,?,?,?)", (draft_id, time.time(), kind, json.dumps(payload)))

    def drafts(self) -> list[sqlite3.Row]:
        with self.lock:
            self.db.row_factory = sqlite3.Row
            try:
                return self.db.execute("SELECT * FROM drafts ORDER BY id").fetchall()
            finally:
                self.db.row_factory = None

    def draft_snapshots(self, draft_id: int) -> list[tuple[float, dict]]:
        rows = self._exec("SELECT t, session FROM snapshots WHERE draft_id=? ORDER BY t", (draft_id,)).fetchall()
        return [(t, unpack(b)) for t, b in rows]

    def draft_recs(self, draft_id: int, kind: str) -> dict | None:
        row = self._exec(
            "SELECT payload FROM recommendations WHERE draft_id=? AND kind=? ORDER BY t DESC LIMIT 1",
            (draft_id, kind),
        ).fetchone()
        return json.loads(row[0]) if row else None

    # -- games ------------------------------------------------------------
    def has_game(self, game_id: int) -> bool:
        return self._exec("SELECT 1 FROM games WHERE game_id=?", (game_id,)).fetchone() is not None

    def add_game(self, game_id: int, created: float, queue: int, champ: int, role: str,
                 win: bool, duration: int, detail: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?)",
            (game_id, created, queue, champ, role, int(win), duration, json.dumps(detail)),
        )

    def _draft_for(self, champ: int, created: float) -> sqlite3.Row | None:
        """The draft you played this game from: same champion, draft ended around game creation."""
        with self.lock:
            self.db.row_factory = sqlite3.Row
            try:
                return self.db.execute(
                    "SELECT * FROM drafts WHERE my_champ=? AND ended BETWEEN ? AND ? ORDER BY ABS(ended - ?) LIMIT 1",
                    (champ, created - DRAFT_GAME_WINDOW, created + DRAFT_GAME_WINDOW, created),
                ).fetchone()
            finally:
                self.db.row_factory = None

    def draft_role_for(self, champ: int, created: float) -> str | None:
        row = self._draft_for(champ, created)
        return row["role"] if row else None

    def link_draft(self, game_id: int, champ: int, created: float, win: bool) -> bool:
        row = self._draft_for(champ, created)
        if not row:
            return False
        self.update_draft(row["id"], game_id=game_id, result="win" if win else "loss")
        return True

    def champ_record(self, role: str | None = None, queue: int | None = None, half_life_days: float = 0,
                     now: float | None = None) -> dict[int, tuple[int, int, float, float]]:
        """Champion id -> (games, wins, eff_games, eff_wins) from your stored games, optionally for one
        role/queue. eff_* weight each game by 0.5 ** (age_days / half_life_days); no decay if half_life_days <= 0."""
        where, args = " WHERE 1=1", []
        if role:
            where += " AND role=?"
            args.append(role)
        if queue is not None:
            where += " AND queue=?"
            args.append(queue)
        if half_life_days <= 0:
            rows = self._exec("SELECT champ, COUNT(*), SUM(win) FROM games" + where + " GROUP BY champ", args).fetchall()
            return {c: (n, w or 0, float(n), float(w or 0)) for c, n, w in rows}
        now = time.time() if now is None else now
        out: dict[int, list] = {}
        for champ, win, created in self._exec("SELECT champ, win, created FROM games" + where, args).fetchall():
            weight = 0.5 ** (max(0.0, now - (created or now)) / 86400 / half_life_days)
            r = out.setdefault(champ, [0, 0, 0.0, 0.0])
            r[0] += 1
            r[1] += 1 if win else 0
            r[2] += weight
            r[3] += weight if win else 0.0
        return {c: (r[0], r[1], r[2], r[3]) for c, r in out.items()}

    def close(self) -> None:
        with self.lock:
            self.db.close()
