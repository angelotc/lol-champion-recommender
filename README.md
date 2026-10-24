# LoL Champion Recommender

Live champ select pick recommender for League of Legends (support-first). Reads the draft from the
local League Client API, pulls master+ / last-14-days matchup and synergy stats from lolalytics,
and ranks picks from your champion pool. Shows a panel docked beside the League client.

## Run
```
uv sync
uv run app.py                       # panel, live
uv run main.py                      # terminal, live
uv run main.py --record drafts/x.jsonl   # live + record the draft
uv run app.py --replay drafts/x.jsonl    # replay a recorded draft
uv run app.py --simulate --allies jinx,leesin --enemies nautilus,caitlyn --bans pyke
uv run pytest
```
Ctrl+Shift+L toggles the panel. Edit `config.toml` for champion pool, lane weights and `k` (sample-size shrinkage).

## Build an .exe
```
uv run --group build pyinstaller league_helper.spec --noconfirm
```
Output: `dist/LeaguePickHelper/`. When packaged, config/cache/logs live in `%LOCALAPPDATA%\league-analysis\`.

## Notes
- lolalytics has no official API; endpoints are undocumented (`lolalytics.py`) and may change. Responses are cached for 24h.
- Reading the League Client API is read-only; nothing is injected into the client or game.
