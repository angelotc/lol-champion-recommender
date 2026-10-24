# LoL Champion Recommender

Live champ select pick recommender for League of Legends. Reads the draft from the local League
Client API, detects the role you're assigned, pulls last-14-days matchup and synergy stats from
lolalytics, and ranks every viable champion you own for that role. Shows a panel docked beside
the League client.

![Live ranked champ select with the panel docked to the right of the League client: Nautilus locked in, picks frozen, top pick Maokai expanded to show its matchup and synergy deltas](docs/client.png)

Each score is the pick's baseline win rate plus weighted matchup deltas vs enemies and synergy deltas with allies;
the lines under each pick show its two biggest contributions. Hover or click a pick to see every
matchup and synergy behind its score: the delta, the games it's based on, and its weighted value.

## Run
```
uv sync
uv run app.py                            # live
uv run app.py --record drafts/x.jsonl    # live + record the draft
uv run app.py --replay drafts/x.jsonl    # replay a recorded draft
uv run app.py --simulate --role support --allies jinx,leesin --enemies nautilus,caitlyn --bans pyke
uv run pytest
```
The panel shows while the League client is in front: in the lobby, in queue and at the ready check
it shows where you are, and in champ select it shows the recommendations. It hides in game.
Ctrl+Shift+L toggles the panel. Ctrl+C in the terminal quits.

## Rank tier
The pill at the top of the panel switches the stats between **Master+**, **GM+** and **Challenger**.
The choice is remembered between runs. Each tier is cached separately for the day.

Higher tiers have far fewer games. Over 14 days, lolalytics lists about 54 support matchups for Thresh
at Master+, 14 at GM+ and none at Challenger. When few matchups are available, the panel says so and the
scores mostly reflect each champion's overall win rate. Lane guessing for enemies always uses
Master+ data.

## Roles
The role always comes from your assigned position in champ select (ranked and normal draft). Queues
that don't assign positions (blind pick, ARAM) show a notice instead of recommendations.

Edit `config.toml` to set:
- **`min_pick_rate`** (default 1.0): candidates are every champion on lolalytics' tier list for your
  role with at least this % pick rate (Master+, 14 days), narrowed to champions you own. About 35-50
  per role at 1%. Lower it to include off-meta picks.
- **Lane weights:** by default your lane opponent counts most, bot and support also weigh the enemy
  duo and their own duo partner, and the jungler matters to every lane. Override them per role under
  `[weights.<role>]`.
- **`k`:** sample-size shrinkage. Lower it if rankings feel too flat with thin master+ samples.

The first draft in a role each day loads stats for that role's candidates, about 6 requests each,
6 at a time, around 10-30 seconds; after that they come from cache.

## Enemy lanes and flex picks
The client doesn't say which lane each enemy plays, so lanes are guessed from each champion's play rates.
Champions with a real second lane (10% or more of their games, e.g. Hwei mid/bot) get a gold dot. Click
any enemy to pick their lane: the picker shows the play rate for each lane, and the other enemies are
re-guessed around your choice. **Auto** goes back to the guess. Your choices reset each draft.

## Build an .exe
```
uv run --group build pyinstaller league_helper.spec --noconfirm
```
Output: `dist/LeaguePickHelper/`. When packaged, config/cache/logs live in `%LOCALAPPDATA%\league-analysis\`.

## Layout
- `app.py`: window, docking and hotkeys. `controller.py` turns client updates into panel views
  and records drafts; `state.py` tracks where you are (lobby, queue, ban, pick, locked, in game).
- `engine.py` + `scoring.py`: candidates and scores. `views.py`: what the panel is sent.
- `data/`: lolalytics (with matchup builds read from the site's pages), Data Dragon, the shared
  fetcher (rate limit, de-duplication) and cache, and `store.py`, a SQLite file in
  `%LOCALAPPDATA%\league-analysis\store.sqlite` that records every draft for backtesting.
- `panel/`: `index.html` + `panel.css`, `js/core.js` (helpers, view router), one file per view in
  `views/`, and `js/demo.js` (open `index.html?demo` in a browser).
- `scripts/render_panel.py`: headless screenshot of any view, for checking UI changes.

## Notes
- lolalytics has no official API; endpoints are undocumented (`lolalytics.py`) and may change. Responses are cached for 24h.
- Reading the League Client API is read-only; nothing is injected into the client or game.
