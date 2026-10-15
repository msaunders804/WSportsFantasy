# PWHL Fantasy: Build Plan for Claude Code

## 0. Context and Hard Constraints

**League facts (2026-27 season):**
- 12 teams, two six-team conferences (East: BOS, HAM, MTL, NY, OTT, TOR; West: DET, LV, MIN, SJ, SEA, VAN).
- 30 games per team, 180 games total. Regular season runs Dec 5, 2026 to Apr 18, 2027.
- One-week league-wide break around the All-Star Game (Mar 7, 2027).
- Training camp rosters are announced starting Oct 18. Preseason starts Nov 22.
- On average a team plays about 1.6 games per week, so weekly matchups will be sparse and high-variance.
- PWHL lists positions as **F, D, G**. There is no C/LW/RW split. Do not invent one.

**Decisions already made:**
- Scoring: Head-to-Head Points (ESPN-style), configurable per league.
- Draft: live snake draft with pick timer and autopick.
- Stack: Python. FastAPI + Jinja2 + HTMX, PostgreSQL. Server-rendered and mobile-first.
- Hosting: Railway (web, worker and Postgres as separate services), multi-user, public URL. App name: Undertow Sports. See `docs/decisions.md`.
- Leagues: 2 leagues, 10 teams each. The leagues are independent, and each one drafts from the full player pool.
- Goalie wins: an OT or shootout win is worth 2/3 of a regulation win.
- Waivers: rolling priority. Trades: commissioner veto.
- No keeper or dynasty support for now. Mobile web app only, no PWA or push notifications.

**Schedule pressure:** Drafts must finish before Dec 5. Working backward:
- **Nov 20:** MVP must be done (accounts, leagues, rosters, draft room, player data).
- **Dec 5:** Live scoring and matchups must be working.
- **Later:** Everything else (trades, waivers polish, playoffs, notifications) can ship during the season, in priority order.

## 1. Architecture

```
            +-------------------+        +------------------+
 HockeyTech | Ingestion Worker  |------->|   PostgreSQL     |
  feed ---->| (scheduler,       |  raw + |  raw_snapshots   |
            |  poller, scorer)  |  norm. |  normalized data |
            +---------+---------+        +--------+---------+
                      | NOTIFY                    |
                      v                           v
            +-------------------+        +------------------+
            | Web App (FastAPI) |<------>|  Browser (HTMX,  |
            | Jinja2, SSE, auth |  SSE   |  SSE extension)  |
            +-------------------+        +------------------+
                    on Railway (TLS provided), built from one Dockerfile
```

- **Two processes, one codebase:** `web` and `worker`. Scheduled jobs run only in `worker`. This avoids duplicate jobs when `web` runs several Uvicorn workers.
- **Real-time fan-out:** use Postgres LISTEN/NOTIFY to push events (score updates, draft picks) to SSE streams. This avoids adding Redis. Revisit only if load requires it, which is unlikely at this scale.
- **ORM and migrations:** SQLAlchemy 2.x with Alembic. Pydantic v2 for settings and API schemas.
- **Scheduling:** APScheduler in `worker`, guarded by a Postgres advisory lock so only one scheduler instance ever runs.
- **Styling:** Tailwind (prebuilt CSS) and minimal Alpine.js only where HTMX cannot cover it, such as the draft timer countdown.
- **Time:** store all times in UTC and render in the user's timezone. Games span ET and PT.

## 2. Data Ingestion

### 2.1 Source
The PWHL publishes stats through **HockeyTech**, the same platform the AHL uses. It is an **unofficial, undocumented** feed accessed with a public client key embedded in the league site.

**Phase 0 must verify all of the following before any schema is finalized:**
1. The endpoints for: season list, team list, rosters, player bios, schedule, game summary/box score, and play-by-play.
2. Which stats are actually reported per game: G, A, PPG/PPA, SHG/SHA, GWG, SOG, PIM, +/-, faceoffs, hits, blocks, TOI. For goalies: SA, SV, GA, decision (W/L/OTL/SOL), shutouts, TOI.
3. How game status is represented (scheduled, in progress, intermission, final) and how quickly box scores update during play.
4. Stable IDs for players, teams, games, and seasons.
5. Whether the 2025-26 season data is retrievable. It is needed for replay testing and draft rankings.
6. How the feed marks a game's result type (regulation, OT, or shootout) and which goalie gets the decision. This is required for the goalie win scoring.
7. Exact dates of the All-Star break and any holiday break, so the fantasy calendar can be confirmed.

**Risks:**
- Terms of use: the feed is not licensed for this purpose. Keep request volume low, cache aggressively, and set an identifying User-Agent.
- Key rotation or schema changes: isolate everything behind a single adapter module. Make the key a config value, never hardcoded.
- Stat gaps: if hits or blocks are not reported, they **must not** appear in scoring. Scoring is driven entirely by config and only uses stats that exist.

### 2.2 Ingestion Layers
1. **Raw (bronze):** store every fetched response as JSON with source URL, fetch time, and content hash. Skip storage when the hash matches the previous fetch. This makes reprocessing and debugging possible without refetching.
2. **Normalized:** idempotent upserts into relational tables keyed by HockeyTech IDs.
3. **Derived:** fantasy points per player per game, computed from normalized game stat lines.

### 2.3 Jobs and Cadence
| Job | Cadence | Purpose |
|---|---|---|
| Season/team/roster sync | Daily, plus manual trigger | Signings, releases, LTIR, position changes |
| Schedule sync | Daily | Postponements, Takeover Tour venue/time updates |
| Live game poller | Every 60 to 120 s, only while a game is live | Semi-live box scores |
| Final reconcile | When a game first reports final, then +6 h and +24 h | Catch late scorer changes |
| Nightly full re-sync | Daily, 05:00 ET | Stat corrections for the past 7 days |
| Scoring recompute | After any stat line change | Update fantasy points and matchup totals |
| Waiver processing | Configurable, default daily 04:00 ET | Process claims |
| Autopick / draft clock | Every second during active drafts | Enforce pick deadlines |

**Rule:** fantasy points are always recomputed from per-game stat lines, never adjusted incrementally. Matchup results are **finalized** a configurable number of days (default 3) after the week ends. After that, stat corrections no longer change the result.

## 3. Data Model (entities, not DDL)

**Real hockey:**
- `season` (id, name, start, end, is_current)
- `pro_team` (id, name, abbrev, conference, logo)
- `player` (id, name, position F/D/G, pro_team_id, jersey, status: active/injured/LTIR/inactive, headshot_url)
- `player_team_history` (player, pro_team, start, end) for mid-season moves
- `game` (id, season, home, away, start_utc, status, venue, is_takeover_tour)
- `skater_game_stats` and `goalie_game_stats` (player, game, plus one column per verified stat)
- `raw_snapshot` (source, url, fetched_at, hash, payload)

**Fantasy:**
- `user` (email, display_name, password_hash, timezone)
- `league` (name, season, commissioner, size, status: setup/drafting/in_season/playoffs/complete, invite_code)
- `league_settings` (roster slots, scoring config JSON, waiver type, trade review rules, playoff format, lock rule)
- `fantasy_team` (league, owner, name, avatar, waiver_priority)
- `league_member` (league, user, role: owner/commissioner/co-commissioner)
- `roster_entry` (fantasy_team, player, acquired_via, acquired_at, released_at). Rosters are history, not a mutable list.
- `lineup_slot` (fantasy_team, date, slot, player). Locked per player at that player's game start.
- `matchup_week` (league, week_no, start_date, end_date, is_playoff, round_no for playoffs). A playoff round covers two consecutive `matchup_week` rows and has one `matchup`.
- `matchup` (league, week, home_team, away_team, home_pts, away_pts, status: scheduled/live/final/finalized, winner)
- `draft` (league, type: snake, rounds, pick_seconds, status, order JSON)
- `draft_pick` (draft, overall_no, round, fantasy_team, player, picked_at, is_autopick). Unique constraint on (draft, overall_no) and (draft, player).
- `draft_queue` (fantasy_team, player, rank) for autopick
- `transaction` (league, type: add/drop/waiver/trade, status, created_at, processed_at) with `transaction_item` rows
- `trade` (proposer, recipient, items, status, review_ends_at, reviewed_by, veto_reason)
- `waiver_claim` (team, add_player, drop_player, priority_at_claim, status)
- `fantasy_points` (player, game, league_scoring_hash, points, breakdown JSON). Cached per scoring config.
- `activity_feed` (league, event type, payload, created_at)
- `audit_log` (actor, action, before/after). Commissioner actions must be auditable.

## 4. Fantasy Rules (defaults, all commissioner-configurable)

### 4.1 Roster (scaled to a small player pool)
The pool is about 276 players (about 36 goalies). Default lineup:
- Starters: **3 F, 2 D, 1 UTIL (F/D), 1 G**
- Bench: 4. IR: 1 (only eligible players).
- Total: 12 players. Each 10-team league uses about 120 of roughly 276 players, which leaves a usable waiver pool.
- Goalies: 10 starting G slots per league against 12 PWHL starters. Goalies will be scarce. Cap rostered goalies at 2 per team so one manager cannot corner the position.

Warn the commissioner when (teams × roster size) exceeds about 50% of the active player pool, or when (teams × G slots) exceeds 12.

### 4.2 Scoring (H2H Points)
The default table below is **ESPN-style and must be verified against ESPN's current default before locking**. Only stats confirmed in Phase 0 are enabled.

| Skater stat | Pts | Goalie stat | Pts |
|---|---|---|---|
| Goal | 2 | Regulation Win | 4 |
| Assist | 1 | OT or SO Win | 2.667 (2/3 of a Win) |
| | | Goal Against | -2 |
| Power Play Point | 0.5 | Save | 0.2 |
| Short-Handed Point | 0.5 | Shutout | 3 |
| Shot on Goal | 0.1 | OT Loss | 1 |
| Hit (if tracked) | 0.1 | | |
| Block (if tracked) | 0.5 | | |

**Goalie win scaling (decided):** this follows the PWHL's 3/2 standings split. Configure it as a multiplier (OT/SO win = Win points × 2/3), not as a second fixed value, so the ratio holds if the commissioner changes Win points. Store points as exact decimals, round only for display (2 places), and decide matchup ties on unrounded totals. Shootout goals do not count as skater goals or as goals against, which matches NHL fantasy convention.

### 4.3 Lineups and Locking
- Each player locks at their own game's puck drop (Sleeper style). Unlocked players can move until their game starts.
- Points count only from players in starting slots at lock time.
- Show games remaining this week for every player. With about 1.6 games per week, this is the most important lineup signal.

### 4.4 Season Calendar
The whole fantasy season, including fantasy playoffs, runs **inside the PWHL regular season**. PWHL playoff games are never used, because only 8 of 12 PWHL teams qualify and most fantasy rosters would stop scoring.

- Fantasy weeks run Monday to Sunday. **Week 1 = Dec 5 to Dec 13** (opening weekend merged into the first full week).
- The All-Star break week has no games and is **skipped**. Any holiday break with few or no games is merged into an adjacent week.
- That gives 18 playable weeks:
  - **Weeks 1 to 12: fantasy regular season**, ending Sun Feb 28. The empty All-Star week (Mar 1-7) is skipped.
  - **Weeks 13 to 18: fantasy playoffs** (Mar 8 to Apr 18), **three 2-week rounds**: Round 1 = Weeks 13-14 (Mar 8-21), Round 2 = Weeks 15-16 (Mar 22-Apr 4), Round 3 = Weeks 17-18 (Apr 5-18). **Decided Oct 12, 2026**: single-week rounds left some PWHL teams with 0 games in a round (Week 16 has 6 games league-wide). Two-week rounds give every PWHL team 2 to 4 games per round, the most even of every layout tested. See `docs/decisions.md`.
- A playoff matchup spans both weeks of its round. Lineups, locks and transactions stay daily/per-game as usual, and the matchup total is the sum over the whole round.
- Playoff format for 10 teams: top 6 qualify. Seeds 1 and 2 get a Round 1 bye. Round 1: 3 vs 6, 4 vs 5. Round 2: semifinals with reseeding. Round 3: championship plus a 3rd-place game.
- Round boundaries are **config, not code**. A check script re-runs the games-per-team-per-round analysis whenever schedule sync detects a change (postponement, Takeover Tour move) and warns the commissioner if any PWHL team falls below 2 games in a round.
- Late-season risk: PWHL teams that have clinched or been eliminated may rest players, especially goalies, in April. Round 3 covers the last two weeks, so flag it for the commissioner before Week 1.

### 4.5 Matchup Generation ("randomized matchup logic")
1. Shuffle teams using a seeded RNG. Store the seed so the schedule is reproducible and auditable.
2. Generate a round robin with the circle method. Repeat cycles until all regular-season weeks are filled.
3. Constraints: no team plays the same opponent in consecutive weeks. Opponent counts differ by at most 1. With 10 teams and 12 regular-season weeks, each team plays everyone once (9 weeks) and 3 opponents a second time. Because that is unequal, schedule strength matters for seeding; consider showing strength of schedule in the UI.
4. League size must be even. Reject odd sizes at league creation, so no bye or median logic is needed.
5. Commissioner can regenerate before Week 1 only.
6. Playoff seeding: record, then total points for, then head-to-head, then a seeded coin flip.

### 4.6 Draft (Live Snake)
- Draft order: randomized (seeded) or set by the commissioner.
- Server-authoritative state. Every pick is a transaction with uniqueness constraints, so double clicks and race conditions cannot double-draft.
- The pick deadline is stored as a timestamp. The worker enforces it. Clients only display a countdown.
- On expiry, autopick takes the highest player from the team's queue, otherwise the default rankings, respecting roster slot limits.
- Supports: pause/resume, commissioner undo of the last pick, offline teams auto-drafting, and reconnect with full state recovery.
- Default rankings: based on 2025-26 fantasy points per game. Expansion team players have no PWHL history, so the commissioner can manually adjust ranks. Be explicit in the UI that rankings are a rough guide.

### 4.7 Waivers, Free Agents, Trades
- Dropped players go on waivers for 48 h (setting).
- Waiver type: **rolling priority only**. Initial order is the reverse of the draft order. A team that wins a claim moves to the back. Do not build FAAB.
- Free agents are added instantly unless their game has started.
- Trades: 24 h review period with **commissioner veto**. No league voting. Trade deadline defaults to the end of Week 11. A commissioner who is also a team owner cannot approve or veto their own trade; the league co-commissioner handles those (one co-commissioner per league).
- Lineup or roster changes must never put a team over roster limits; validate on the server.

## 5. UI (Sleeper-style tabs, mobile-first)

Bottom tab bar on mobile, top tabs on desktop: **Matchup | Team | Players | League**. Top bar has a league switcher.

- **Matchup:** your team vs opponent, side by side by slot. Live point totals via SSE. Per-player status: yet to play / live / final, with current stat line. Week selector. Projected-remaining shown only if projections exist.
- **Team:** lineup editor (tap a player, then tap a valid slot), lock indicators, games this week per player, bench, IR, add/drop, pending waiver claims, trade offers.
- **Players:** searchable, filterable table (position, pro team, availability, conference). Sort by fantasy points (season, last 7/14 days, per game). Player detail sheet: game log, schedule, ownership %, add/claim/trade buttons.
- **League:** standings, schedule and results, playoff bracket, activity feed (adds, drops, trades), league settings (read-only to members, editable by commissioner), invite link.
- **Draft room:** separate full-screen page. Board grid (rounds × teams), on-the-clock banner with timer, available players with search, my queue (drag to reorder), my roster by slot, live pick feed.
- **Commissioner tools:** create league wizard, edit settings before draft, set draft order, force add/drop, edit lineups, reverse transactions, regenerate schedule before Week 1, manually finalize a matchup. Everything goes to the audit log.

## 6. Auth, Security, Ops
- Email + password (argon2) with server-side sessions in secure HttpOnly cookies, plus CSRF protection on all state-changing requests. Join leagues by invite link.
- Authorization checks on every endpoint: league membership, team ownership, commissioner role.
- Rate limiting on login and on draft/transaction endpoints.
- Railway project with `web`, `worker` and Postgres services from one Dockerfile; migrations run as the web pre-deploy command. Docker Compose is for local development only. Secrets come from environment variables.
- Nightly `pg_dump` to off-site storage, with a documented restore test.
- Structured logging. A health page showing last successful fetch per job and feed error counts. Alert (email is enough) if live polling fails during a game window.

## 7. Testing Strategy
- **Recorded fixtures:** capture real HockeyTech responses once and replay them in tests. No live network in CI.
- **Scoring golden tests:** hand-computed expected points for a set of real box scores.
- **Season replay mode:** run the full 2025-26 season through ingestion, scoring, and matchups with a simulated clock. This is the main integration test and the way to validate live behavior before December.
- **Draft concurrency tests:** simultaneous picks, timer expiry during a pick, reconnects.
- **Property tests** for schedule generation (balance and no back-to-back repeats).
- **Smoke tests on mobile viewport** for each tab.

## 8. Build Phases (each ends with passing tests and a demo)

| Phase | Scope | Target |
|---|---|---|
| 0 | Feed discovery spike: document endpoints, available stats, ID stability, status values. Record fixtures. Write `docs/feed.md`. | Oct 16 (done) |
| 1 | Project skeleton, Dockerfile, config, DB, Alembic, CI, `CLAUDE.md` conventions | Oct 19 (built, PR #1; CI file pending token scope) |
| 2 | Ingestion: teams, rosters, schedule, game stats, raw snapshots, worker scheduler | Oct 26 (done, PR #2: adapter, tables, worker jobs, scoring engine; 2025-26 season replay clean) |
| 3 | Auth, leagues, invites, teams, league settings, commissioner wizard | Nov 2 (foundations built, PR open; wizard, invite emails, password reset still to do) |
| 4 | Players tab, rankings from 2025-26 data, player detail | Nov 6 |
| 5 | Live snake draft room (core MVP risk; budget the most time here) | Nov 16 |
| 6 | Hardening, deploy to Railway, mock draft with real users | Nov 20 |
| 7 | Scoring engine, lineups and locks, matchup generation, Matchup tab with SSE | Dec 3 |
| 8 | Free agents and waivers | Dec 13 |
| 9 | Trades, activity feed, standings polish | Jan 2027 |
| 10 | Playoffs and bracket | Before final regular-season weeks |
| 11 | Nice-to-haves: email notifications, projections, chat, dark mode | Ongoing |

Out of scope for this season: keepers/dynasty, FAAB, league trade voting, PWA install and push notifications. Do not build hooks for them now.

## 9. Instructions for Claude Code
- Build one phase at a time. Do not start a phase until the previous phase's tests pass.
- Put all HockeyTech access in one adapter module behind an interface so the source can be swapped out.
- Make the scoring engine a pure function: (stat line, scoring config) ? points and breakdown. No database access inside it.
- Put business rules (roster validation, lock checks, waiver processing, schedule generation) in a service layer with no HTTP or template code, so they can be unit tested.
- Make every money-like or contested operation (draft pick, waiver claim, trade acceptance) a single DB transaction with constraints, not application-level checks alone.
- Never hardcode the season, team list, or stat list. Read them from the database or config.
- Keep a running `docs/decisions.md` recording each design decision and the reason for it.

## 10. Remaining Open Items
1. Confirm the default scoring table against ESPN's current hockey points defaults (before Phase 7).
2. Results from Phase 0: stat availability (hits, blocks), result-type field for OT/SO, break dates. Update sections 4.2 and 4.4 with what is found.
3. Who is the commissioner and co-commissioner for each league (before Phase 3 data entry).
