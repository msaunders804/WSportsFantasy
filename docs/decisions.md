# Decisions

## 2026-10-12: Hosting is Railway
Decided by Megan. Web, worker and Postgres as separate services in one Railway project, built from a Dockerfile with env-var config. Drops the Compose and Caddy parts of Plan.MD section 6; Railway provides TLS. Use a Postgres database separate from the PWHL analysis one, and a direct (non-pooled) connection so LISTEN/NOTIFY works. App name: Undertow Sports.

## 2026-10-12: Playoff timing (DECIDED by Megan: three 2-week rounds)
Plan.MD sections 3, 4.4, 4.5 and 4.7 updated to match. Commissioners should be told before the drafts.
Problem: with one-week rounds, Week 16 (Mar 29 to Apr 4) has 6 games league-wide and Las Vegas plays 0. Games per PWHL team in any one week range 0 to 3, so single-week rounds are noisy by nature.

Searched every layout of 3 playoff rounds of 1 to 3 weeks each, ending Apr 18 or Apr 11, using the 2026-27 schedule fixture. Best by far: **three 2-week rounds**.

| Round | Fantasy weeks | Dates | Games per PWHL team |
|---|---|---|---|
| Quarterfinal (seeds 3-6) | 13-14 | Mar 8 to Mar 21 | 2 to 4 |
| Semifinal | 15-16 | Mar 22 to Apr 4 | 2 to 4 |
| Final and 3rd place | 17-18 | Apr 5 to Apr 18 | 2 to 4 |

- Every team plays at least 2 games in every round. Current plan (1-week rounds) has a team with 0 in round 1.
- Spread is at most 2 games per team per round, the lowest of any layout. 3-week rounds are worse (spread 3-4).
- The season ends on the real last day, so end-of-season rest risk stays as in Plan 4.4.
- Cost: regular season becomes 12 weeks (Weeks 1-12) instead of 15. With 10 teams each plays 9 opponents once plus 3 repeats. Trade deadline moves from end of Week 13 to end of Week 11. Longer rounds also reduce variance, which suits about 1.6 games per week.
- Alternative if you want a longer regular season: 14 weeks with rounds of 2, 1, 1 weeks (Mar 22 start). Same worst-round spread but Week 17-18 rounds have a team with only 1 game.

The schedule can still change (postponements), so playoff round boundaries must be config, and a check script should re-run this analysis whenever the schedule sync detects changes.

## 2026-10-12: Phase 1 tooling choices
- Package management with `uv` (lockfile committed, Python pinned to 3.12 via `.python-version` to match the Docker image).
- Single Docker image for both services. `web` is the default command; `worker` overrides the start command to `python -m undertow.worker.main`. Migrations run as Railway's `preDeployCommand` on `web` only, so they cannot race.
- Worker takes a Postgres session-level advisory lock before starting APScheduler, so an accidental second worker instance idles instead of duplicating jobs.
- Local dev Postgres is published on host port 5433 because 5432 is already used on the shared dev box.
- Baseline Alembic revision is empty on purpose; tables arrive with Phase 2 once the schema is checked against the fixtures.
- Tailwind and Alpine are deferred until the first real page (Phase 3). Pages are unstyled for now.

## 2026-10-12: Phase 2 ingestion and scoring choices
- Primary keys of real-hockey tables are the HockeyTech IDs. Players found only in a box score get a bare row (insert-if-missing); roster sync is authoritative and overwrites.
- Game start time comes from `GameDateISO8601` (has a real offset). The feed's `date_time_played` is local time labelled `Z` and is ignored.
- Raw snapshots store the decoded JSON (JSONP stripped) with a SHA-256 of the canonical form; a response identical to the latest for the same URL is not stored again. Snapshot URLs never contain the API key.
- Shutout is derived: the team's only goalie to play, with 0 goals against. Goalie decision is one per (game, goalie) from `goalieLog` (W > OTL > SOL > L when stints differ). REG/OT/SO comes from the game's final status text.
- PP/SH points and GWG are derived from the goal list, which already excludes shootout goals.
- Conference (East/West) is a small data file keyed by feed team code (`data/conferences.json`), since the feed has no conference. Note LV is `VGS` in the feed.
- Scoring uses exact `Fraction` arithmetic so the 2/3 OT-win multiplier stays exact. `ScoringConfig.from_dict` treats a missing key as 0 (not scored), so league settings must always store the full `to_dict()`.
- Not yet built: `player_team_history` (needs mid-season move data), roster injury/LTIR status (not in the feed), and worker job registration for the syncs.

## 2026-10-13: Worker jobs and season replay
- Jobs (all in `worker`, each recorded in `job_run`, failures never stop the scheduler): `daily_sync` 04:30 ET (seasons, teams, schedule, rosters), `nightly_resync` 05:00 ET (schedule, then re-fetch the last 7 days of games), `poll_live` every 90 s, `reconcile_finals` every 30 min. Ingestion jobs are only registered when `HOCKEYTECH_KEY` is set. Seasons to sync come from `INGEST_SEASON_IDS` (comma-separated, default `11`).
- The live poll makes no requests unless a game is inside its window (10 min before start to 5 h after). It also picks up games the schedule already calls final but whose box score we never captured (`final_at` is null), so a schedule sync that flips a game to final cannot cause a missed box score.
- `game.final_at` is stamped the first time we see a box score final; `reconcile_count` tracks the +6 h and +24 h follow-up fetches.
- Season replay (`python -m undertow.ingest.replay`) drives these same jobs on a simulated clock, caching every response under `data/replay_cache/` (gitignored). The first run hits the live feed (throttled, about 130 requests); later runs are offline. Integrity checks: one W and one matching loss decision per game (L, OTL or SOL by result type), skater goals per team equal the team score minus the shootout-winning goal, and the opposing goalies' goals against match too.
- Not built: scoring recompute (needs leagues, Phase 7) and email alerts when polling fails during a game window (needs the email provider). `job_run` has the data for both.
- Replay findings that changed the code: box scores say `Final OT1` (schedule: `Final OT`), so result type is matched loosely; empty-net goals are stored per skater (`en_goals`) because they are not charged to the goalie; an empty goalie result means no decision. Details in `docs/feed.md`.
- Logging: the `httpx` logger is set to WARNING in both entry points because it logs full request URLs (including the API key) at INFO.

## 2026-10-14: Railway service settings are set in the dashboard
- First deploy: Railway did not apply `railway.toml` to the services (the web service showed no pre-deploy command or healthcheck), so migrations never ran and the worker failed with `relation "game" does not exist`. Fix: set the values in each service's dashboard settings, which take precedence over the file.
- `web`: Pre-deploy Command `alembic upgrade head`, Healthcheck Path `/healthz`. Start command is left blank so the Dockerfile `CMD` (uvicorn) is used.
- `worker`: Start Command `python -m undertow.worker.main`. Pre-deploy Command and Healthcheck Path stay empty (no HTTP server; migrations run from `web` only so they cannot race).
- `railway.toml` is kept as documentation of the intended web settings. If a new service or environment is created, re-check these fields by hand, and confirm in the deploy log that the pre-deploy step ran.
- Variables: `HOCKEYTECH_KEY` and `HOCKEYTECH_USER_AGENT` are set on `worker` only (nothing in `web` touches the feed). `DATABASE_URL` uses the `postgresql+psycopg://` scheme on the private (internal) host on both services.
- Branching: one branch per phase, each PR opened directly against `main` (stacked PRs merged into each other's branches left Phase 2 off `main` until a follow-up PR).
