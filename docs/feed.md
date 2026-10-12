# HockeyTech feed notes (Phase 0)

Probed Oct 12, 2026 with ~25 requests total, 2 s apart, User-Agent `UndertowSports-research/0.1 (contact email)`. Recorded responses are in `tests/fixtures/hockeytech/`. The feed is unofficial and undocumented; everything here was observed, not promised.

## Access
- Base: `https://lscluster.hockeytech.com/feed/index.php`
- Common params: `key=<appKey>&client_code=pwhl&fmt=json`
- The key and client code are embedded in the public `thepwhl.com/en/stats/` page (`var appKey`, `var clientCode`). **The key goes in config (`HOCKEYTECH_KEY`), never in code.** It is not repeated in this doc. The recorded fixtures do contain it in their `Parameters` block (it is the public site key, not a secret, but do not copy it into code or config files in the repo).
- Two response shapes: `modulekit` returns `{"SiteKit": {...}}` plain JSON. `statviewfeed` returns JSON wrapped in parentheses `( ... )` (JSONP style), so the adapter must strip them. `feed=gc` returns `{"GC": {...}}`.
- All numbers inside `modulekit` are **strings**. `statviewfeed gameSummary` uses real ints. The adapter must normalize.

## Endpoints verified

| Need | Request | Fixture |
|---|---|---|
| Season list | `feed=modulekit&view=seasons` | `seasons.json` |
| Teams | `feed=modulekit&view=teamsbyseason&season_id=N` | `teams_2026-27.json` |
| Schedule | `feed=modulekit&view=schedule&season_id=N` | `schedule_*.json` |
| Roster (per team) | `feed=modulekit&view=roster&team_id=T&season_id=N` | `roster_bos_*.json` |
| Player bio/career | `feed=statviewfeed&view=player&player_id=P&season_id=N&site_id=0&lang=en` | `player_52.json` |
| **Box score (primary)** | `feed=statviewfeed&view=gameSummary&game_id=G` | `gamesummary_*.json` |
| Game summary (alt.) | `feed=gc&tab=gamesummary&game_id=G` | `gc_gamesummary_210.json` |
| Play-by-play | `feed=gc&tab=pxpverbose&game_id=G` | `pxp_210.json` |
| Season leaders table | `feed=statviewfeed&view=players&...` | **not usable yet**: returned a header-only response with the parameters I tried. Not needed, since we compute totals from game lines. |

## IDs and seasons
- Player, team, game and season IDs are stable integers. The same player ID (e.g. 52) appears in rosters, box scores, and play-by-play. `person_id` is a separate cross-season ID (52 -> 54).
- Seasons: **11 = 2026-27 Regular Season** (feed says Dec 4, 2026 to Apr 19, 2027), **10 = 2026-27 Pre-Season**, 9 = 2026 Playoffs, **8 = 2025-26 Regular Season**, 7 = 2025-26 Preseason, 6 = 2025 Playoffs, 5 = 2024-25 Reg, and so on back to season 1. **2025-26 (season 8) is fully retrievable, 120 games, all Final.** Good for replay testing and rankings.
- Teams 2026-27 (12): BOS 1, MIN 2, MTL 3, NY 4, OTT 5, TOR 6, SEA 8, VAN 9, DET 10, HAM 11, **LV = id 12 with code `VGS`**, SJ 13. ID 7 is unused. Use the **ID** as the key. Abbreviations differ from the plan (VGS, not LV).
- Team names in the feed: "PWHL Las Vegas", "PWHL San Jose", "PWHL Detroit", "PWHL Hamilton" (placeholders, no nickname yet), and "Montréal Victoire" (accent).

## Game status and result type (schedule feed)
- `status`: `1` scheduled, `4` final. In-progress and intermission values are **not yet observed** (no live game to look at). Also watch `started`, `final`, `period`, `game_clock`, `intermission`.
- `game_status` is a display string: `"Final"`, `"Final OT"`, `"Final SO"` (2025-26: 90 / 20 / 10 games). **This is the regulation vs OT vs SO marker** needed for goalie win scaling. Scheduled games show the start time here instead.
- `overtime` and `shootout` flags also exist per game. `hasShootout` and an `OT1` period id appear in the box score.
- `last_modified` exists per game, useful to skip unchanged games.
- Postponements, Takeover Tour: `venue_name`, `venue_location`, `schedule_notes`, `time_tbd`, `date_tbd` are all present; `timezone` is `America/Toronto` for 173 games and `America/New_York` for 7. `location` is a venue ID. **Nothing flags a Takeover Tour game directly**; infer from venue != home arena.

## Stats available per game (box score)

**Skaters** (`homeTeam.skaters[].stats`): `goals, assists, points, penaltyMinutes, plusMinus, faceoffAttempts, faceoffWins, shots, hits, blockedShots, toi`. Also `starting` and `status`.

**Goalies** (`goalies[].stats`): `timeOnIce, shotsAgainst, goalsAgainst, saves`. Goalies who did not play have zeros and `timeOnIce: null`.

Confirmed available: **G, A, SOG, PIM, +/-, hits, blocks, faceoffs, TOI, SA, SV, GA.** So **hits (0.1) and blocks (0.5) can stay in the default scoring table.**

**Not in the box line, must be derived:**
- **PPG/PPA, SHG/SHA:** each goal under `periods[].goals[]` has `properties.isPowerPlay`, `isShortHanded`, `isEmptyNet`, `isPenaltyShot`, `isGameWinningGoal`, plus `scoredBy` and `assists[]`. Power-play points = goals + assists on goals with `isPowerPlay=1` (same for short-handed). Use the goal list, not the box line.
- **GWG:** `properties.isGameWinningGoal`.
- **Shutout:** derive: goalie played the full game and `goalsAgainst == 0`. Not a field.
- **Goalie decision:** `goalieLog[]` has one row per goalie stint with `result` of **`W`, `L`, `SOL`** (and presumably `OTL`; not yet seen, so verify with an OT-loss fixture). Combined with `game_status` (`Final OT` / `Final SO`), this gives regulation win vs OT/SO win. In game 210 the winning goalie has several stints all marked `W`; take **one** decision per (game, goalie), not one per stint.
- **Shootout goals:** verified on game 227 (SO). Skater `goals` and goalie `goalsAgainst` **exclude** shootout goals (skater sum 1, GA sum 1 for the winner), and `periods[].goals[]` lists only regulation/OT goals. Only the **team** `stats.goals` and the schedule's `home/visiting_goal_count` add the +1 for the shootout winner. Shootout attempts are in `shootoutDetails`. So scoring from player lines already matches the plan's convention; never use team goal totals for player scoring. One game checked; confirm on a second SO game.

Position values in box scores and rosters are **granular: `C, LW, RW, LD, RD, G`** (the roster feed also has a bare `F`/`D` for a few unsigned players). Map to **F** (C, LW, RW, F), **D** (LD, RD, D), **G** in the adapter. Keep the raw value in the raw snapshot.

## Roster feed
- `status` on 2026-27 preseason rosters includes `Signed`, `Camp Invitee`, `None`, so it is not the plan's active/injured/LTIR enum. Needs a mapping, and the injury and LTIR signal is **not confirmed to exist**. Check again once training-camp rosters are announced (Oct 18).
- Roster feed is per team. 12 requests per full sync. Contains `player_id, person_id, first_name, last_name, tp_jersey_number, position, shoots, birthdate, height, status, team_id, rookie, veteran_status`. Headshots: `assets.leaguestat.com/pwhl/{240x240|120x160}/{player_id}.jpg`.

## 2026-27 calendar (180 games, 30 per team, confirmed)
- First game **Sat Dec 5, 2026**, last game **Sun Apr 18, 2027**. (The season record says Dec 4 to Apr 19; use the game dates.)
- **All-Star break:** no games Feb 28 to Mar 9. The fantasy week Mon Mar 1 to Sun Mar 7 is empty and skipped, as planned. The feed doesn't label it.
- **No holiday break:** games every week Dec 7 to Feb 28. Lowest weeks: Dec 5 opening (6 games, merged into Week 1), Mar 29 week (6 games), Mar 15 (8), Feb 8 (8).
- Games per fantasy week with the plan's calendar: W1 16 (Dec 5-13), then W2 9, W3 9, W4 11, W5 10, W6 10, W7 10, W8 9, W9 11, W10 8, W11 12, W12 11, W13 10 (Mar 8-14), W14 8, W15 11, **W16 6 (Mar 29-Apr 4)**, W17 10, W18 9. That matches the plan's 18 weeks. (W2-W12 from the weekly counts; week labels are from the plan's numbering.)

### Playoff-week imbalance (Plan 4.4 check) — **needs your decision**
Games per PWHL team:
- **W16 (Mar 29-Apr 4):** 1 game for everyone, except TOR with 2 and **LV with 0**. Every Las Vegas player scores nothing in the first fantasy playoff round.
- **W17:** most teams 2; HAM 1, NY 1, OTT 1, LV 1.
- **W18:** BOS, DET, NY, OTT, TOR, LV 2; HAM, MIN, MTL, SEA, SJ, VAN 1.
This is a bigger imbalance than the plan assumed. See [decisions.md](decisions.md) for the proposed fix (three 2-week playoff rounds). Other options: the "playoffs end one week before the regular season ends" setting (Plan 4.4), or 2-week playoff rounds. Raise with the commissioners before the draft, since it changes player value (especially for LV players).

## Open items for later
1. In-progress and intermission `status` values and update latency: needs a live game. The first preseason game is a chance to observe it (Nov 22 start per Plan; the feed's preseason season runs Oct 1 to Nov 30).
2. `OTL` goalie result and a second shootout game: grab fixtures when seen.
3. Bulk "season stats" table (`view=players`): not needed for scoring, skip unless rankings require it.
4. Roster status for injuries/LTIR: see above.
5. Terms of use unchanged: low volume, aggressive caching, identifying User-Agent.
