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
