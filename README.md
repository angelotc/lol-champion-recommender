# LoL Companion

A panel that docks beside the League client and ranks your best picks in champ select, using
matchup and synergy stats, your team and theirs, and your own record. After you lock in it shows
runes and builds; in game, a compact build panel.

![Panel docked beside the League client in champ select](docs/client.png)

## Install
Download **[LoLCompanion-win-Setup.exe](https://github.com/angelotc/lol-companion/releases/latest/download/LoLCompanion-win-Setup.exe)**
and run it. It installs for your Windows account (no admin) and updates itself.

Portable instead: [LoLCompanion.zip](https://github.com/angelotc/lol-companion/releases/latest/download/LoLCompanion.zip)
(unzip, run `LoLCompanion.exe`; no auto-updates).

Windows may warn that the app is unrecognized (it isn't code-signed): **More info → Run anyway**.

## Use
The panel appears on its own while the League client is in front and hides in game.

| Key | Does |
|---|---|
| **Ctrl+X** | Toggle the panel (client in front) or the in-game panel (in game) |
| **Ctrl+Shift+L** | Toggle the panel from any window |

The **tray icon** (bottom-right) shows/hides the panel, checks for updates, opens settings, turns
on Start with Windows, and quits.

| Queue | Bans | Picks |
|---|---|---|
| <img src="docs/views/1-queue.png" width="240"> | <img src="docs/views/2-bans.png" width="240"> | <img src="docs/views/3-picks.png" width="240"> |
| Stats preload for your roles. | Ban suggestions (optional). | Draft win estimate, then ranked picks. |

| Flex lanes | Build | In game |
|---|---|---|
| <img src="docs/views/4-flex.png" width="240"> | <img src="docs/views/5-build.png" width="240"> | <img src="docs/views/6-ingame.png" width="240"> |
| Click an enemy to set their lane. | Runes, spells, items vs your lane. | Core build, gold to next item, skills. |

- **Picks** start at the first locked pick. Hover a pick to see every term behind its score.
- **Rank tier** (pill at the top): Master+, GM+ or Challenger. Higher tiers have fewer games.
- **Enemy lanes** are guessed from play rates; flex picks get a gold dot. Click to override.
- **Build:** each option shows its Δ vs your lane opponents; the best is tagged **BEST VS LANE**.
- **In game** needs borderless or windowed mode. Clicks pass through it.

## Settings
`config.toml` (tray → Open settings; installed copy lives in `%LOCALAPPDATA%\league-analysis\`):

| Key | Default | |
|---|---|---|
| `min_pick_rate` | 1.0 | Min pick rate % to be a candidate. Lower for off-meta picks. |
| `show_ban_suggestions` | false | Show bans during the ban phase. |
| `comfort_only` | false | Only recommend champions you've played. |
| `[comfort]` | | `weight` (0 = off), `k`, `history`, `half_life`, `mastery`, `mastery_cap` |
| `[weights.<role>]` | | Per-role lane weights. |
| `[odds]` | | Draft win estimate weights. |

## How picks are scored
One score per candidate, in win-rate points:

```
score(c) = base + Σ vs + Σ with + you + mastery

base    = shrink(wr(c) − avg_wr, games(c), k)                   × w_base
vs      = shrink(Δ(c vs enemy in lane L), games, k)             × w_enemy[L]   each known enemy
with    = shrink(Δ(c with ally in lane L), games, k)            × w_ally[L]    each ally (× 0.5 if hovered)
you     = shrink(your_wr(c) − wr(c), your_games(c), 20) × 0.5   × w_comfort
mastery = min(0.15 × ln(1 + points / 1000), 1.0)                × w_comfort

shrink(δ, n, k) = δ × n / (n + k)     k = 500: a 500-game sample counts half
your games are weighted 0.5^(age / half_life), half_life = 90 days
```

- **Δ**: lolalytics' normalized delta: how much better `c` does with/against a champion than
  both champions' win rates predict.
- **wr, games, avg_wr**: your role, your tier, last 14 days.

| Lane weight | your lane | duo lane (bot/sup) | jungle | other |
|---|---|---|---|---|
| `w_enemy` | 1.0 | 1.0 | 0.5 | 0.3 |
| `w_ally` | – | 1.0 | 0.5 | 0.3 |

- **Ban threat** = `pick_rate × (harm to your picks + strength × w_enemy)`, summed over lanes.
- **Build Δ** = `shrink((wr_option_vs − wr_vs) − (wr_option − wr), games, 300)`, hidden under 100 games.
- **Draft win estimate**: champion strength (half weight) + cross-team matchups (1.0 same lane, 0.7
  bot duos, 0.35 other) + synergy (half weight), capped 25–75%. Not yet calibrated.

## APIs
| API | Used for |
|---|---|
| [League Client API](https://developer.riotgames.com/docs/lol#league-client-api) (local, read-only) | Champ select (`/lol-champ-select/v1/session`, pickable champions), game phase, lobby, match history, mastery, patch version |
| [Live Client Data API](https://developer.riotgames.com/docs/lol#game-client-api) (`127.0.0.1:2999`) | In game: your champion, items, gold, level, and each player's champion/position. No enemy items or timers. |
| [lolalytics](https://lolalytics.com) (unofficial, may change) | Win rates, matchups, synergies, builds. Cached 24h. |
| [Data Dragon](https://ddragon.leagueoflegends.com) + [CommunityDragon](https://raw.communitydragon.org) | Champion, item and rune data and images |
| GitHub Releases (via [Velopack](https://velopack.io)) | Auto-updates |

Nothing is injected into the client or the game.

## Develop
```
uv sync
uv run app.py                                  # live
uv run app.py --record drafts/x.jsonl          # live + record
uv run app.py --replay drafts/x.jsonl          # replay
uv run app.py --simulate --role support --allies jinx --enemies nautilus
uv run pytest
uv run --group build pyinstaller league_helper.spec --noconfirm   # → dist/LoLCompanion/
```

Every push to `main` runs the tests, builds, and publishes version `0.1.<run>` (installer +
update feed) plus the portable zip.

| Path | |
|---|---|
| `app.py`, `tray.py`, `updater.py` | Window, hotkeys, tray icon, auto-updates |
| `controller.py`, `state.py` | Client updates → panel views; draft stage |
| `engine.py`, `scoring.py` | Candidates and scores |
| `data/` | lolalytics, Data Dragon, fetcher/cache, `store.py` (SQLite of drafts and games) |
| `panel/` | The panel UI (`index.html?demo` runs in a browser) |
| `scripts/` | `simulate_views.py` (README screenshots), `render_panel.py`, `make_icon.py` |
