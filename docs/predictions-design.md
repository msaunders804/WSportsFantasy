# Predictions: data and storage design

Status: DRAFT for Megan's review, 2026-11-12. Nothing is built. Covers where prediction data lives, who can write it, and how it reaches the web app. The modelling method is a separate document, to follow once the data coverage audit has run.

## Decisions already made (Megan, 2026-11-12)
- One Railway Postgres database, two schemas: `public` (existing) and `analytics` (new).
- Predictions are **public** on the web app.
- Shown as **probabilities and fair-odds equivalents only**. Not betting content: no sportsbook lines, no stake or pick language.
- Only **derived ratings** are published. Raw third-party data (NCAA, Elite Prospects, international) stays out of production.

## Goals
1. Weekly predictions for every PWHL game, founded on stored data and reproducible.
2. A permanent public track record (calibration), which only works if predictions can never be edited after the fact.
3. Model work can never damage fantasy data (accounts, leagues, drafts).
4. The web app stays simple: it reads precomputed rows and never runs model code.

## Layout

| Schema | Holds | Written by | Read by |
|---|---|---|---|
| `public` | Existing hockey tables (season, pro_team, player, game, skater/goalie game stats, raw_snapshot, job_run), fantasy tables, plus new `shot_event` | worker, web | everything |
| `analytics` | Model runs, ratings, predictions, availability log | the prediction job only | web (read) |

`public` is not moved or renamed. `analytics` tables may hold foreign keys to `public.player`, `public.pro_team` and `public.game` (same database, so this works and keeps IDs honest).

### Where raw research data lives
Local only, never in Railway: licensed or scraped third-party records (NCAA, Elite Prospects, international, European leagues). The prediction job reads them from local files when it trains. Production receives only the outputs: ratings, run metadata, predictions. This keeps third-party terms of use out of the public service and keeps the production database small.

## New and changed tables

### public.shot_event and public.penalty_event (built, 2026-11-12; filled by ingest)
`shot_event`: one row per shot on net (goals included), blocked shot or penalty shot, with period, seconds within the period, shooting team, shooter, goalie (null on an empty net), blocker, raw x/y, shot type, the feed's own quality code, and goal / empty-net / power-play / short-handed flags. A goal is one row (the feed's `shot` merged with its `goal` event). `penalty_event`: one row per penalty with the clock, team, player, minutes and class, so strength state can be rebuilt in analysis. Migration 0010 adds both (new tables only). Raw responses are in `raw_snapshot`, so faceoffs, hits and anything else can be derived later without refetching. Findings on all 282 games from 2024 to 2025-26 are in [feed.md](feed.md): coordinates are normalised per team, the feed has no missed-net shots, and shots on net agree with box scores. Loaded with `ingest/backfill.py --pbp`, which needed a `play_by_play` method on the feed adapter.

### analytics.model_run
One row per execution. `id`, `model_name`, `model_version` (semver), `code_ref` (git commit), `params` (JSONB), `data_through` (latest result included), `created_at` (database clock), `published` (bool), `notes`. Everything below points at a run, so any number on the site can be traced to exactly what produced it.

### analytics.player_rating
`model_run_id`, `player_id`, component ratings (for example offence, defence, goaltending as the model needs), `rating_mean`, `rating_sd`, `evidence_tier` (`pwhl_3plus`, `pwhl_1_2`, `external_only`, `none`). The tier is published with the rating so readers see how much it rests on. No raw external stats are stored here.

### analytics.team_rating
`model_run_id`, `pro_team_id`, attack and defence strength with uncertainty, plus the lineup assumptions used (JSONB).

### analytics.game_prediction (append-only)
`id`, `model_run_id`, `game_id`, `made_at` (database default, never supplied by the client), `p_home_reg_win`, `p_home_ot_so_win`, `p_away_ot_so_win`, `p_away_reg_win`, `exp_goals_home`, `exp_goals_away`, interval bounds, `lineup_assumptions` (JSONB: who was assumed out, starting goalie). Unique on `(model_run_id, game_id)`. Win probability and fair odds are derived from these columns at display time and are not stored.

### analytics.availability_status
Injury and availability log. `player_id`, `status` (`active`, `day_to_day`, `out`, `ir`, `unknown`), `effective_from`, `effective_to`, `source_url`, `reported_at`, `entered_by`. Status only, no medical detail, and public sources only. Entered manually at first; an extraction step can follow. Prediction runs record which rows they used.

### Views
- `analytics.latest_game_prediction`: the most recent pre-game prediction per game from a published run. The web app reads this.
- `analytics.calibration_by_bucket`: predictions joined to `public.game` results, grouped by probability bucket (predicted vs actual rate, count). Drives the track-record page.

## Integrity rules (enforced in the database, not just code)
1. **Append-only predictions.** The job's role has `INSERT` and `SELECT` on `game_prediction` and `model_run`, no `UPDATE` or `DELETE`. Publishing a run is the one allowed change to `model_run`, via a dedicated function.
2. **No after-the-fact predictions.** A trigger rejects an insert if `made_at` is at or after the game's `start_utc`. A late run can only affect games that have not started.
3. **Database timestamps only.** `made_at` and `created_at` default to `now()` in Postgres.
4. **Corrections are new runs**, never edits. A bad run is unpublished, not deleted, and the site can say so.

## Roles and access
| Role | `public` | `analytics` |
|---|---|---|
| Existing app role (web, worker) | as today | read |
| `undertow_model` (new, prediction job only) | `SELECT` on hockey tables only | insert per the rules above |

The model role cannot see accounts, leagues or sessions, and cannot write any `public` table. Role passwords live in Railway variables, never in migrations or git. The migration creates the schema and tables; creating the role and granting privileges is a documented one-time ops step run by you in the Railway Postgres console (a script I'd supply). Alembic's version table stays in `public`, and autogenerate must be told about the `analytics` schema.

## How the job runs
- **Code home:** a new package `src/undertow/predict/` (features, model, batch entry point). It reads through `undertow.data`, never the feed, and it is the only code that writes `analytics`. `content/` is unchanged. Dependency direction stays one-way.
- **Where it runs, first:** on your machine against Railway, using `railway run` with the model role's URL. Training uses local research data, so this fits.
- **Later:** a scheduled third Railway service on the same image, if and when all inputs it needs are in production. Not needed for launch.
- **Cadence:** one scheduled run each week covering the coming week's games, plus an optional day-of refresh when lineup or availability news changes. Each is its own `model_run`; the site shows the latest pre-game prediction and keeps the earlier ones visible on the game page.

## Web presentation (public, no login)
Routes, all read-only:
- `/predictions`: this week's games with win probabilities, fair odds, expected goals and an uncertainty note.
- `/predictions/game/{id}`: one game, with the full prediction history and the assumptions used.
- `/predictions/ratings`: player and team ratings with their evidence tier.
- `/predictions/track-record`: the calibration table and chart, and results by model version.

**Fair odds:** computed as 1/p (decimal) and shown with a fixed caption: "Fair odds are the probability expressed as odds, with no sportsbook margin. This is analysis, not betting advice." No sportsbook data is stored or shown, and there is no staking or pick language. Pages are cached, since data changes at most a few times a week.

## Build order
1. This doc reviewed; entry added to [decisions.md](decisions.md).
2. Coverage audit (set `HOCKEYTECH_KEY`, run [analysis/pwhl_coverage_audit/audit.py](../analysis/pwhl_coverage_audit/audit.py)).
3. Historical backfill, seasons 1-8, into the local dev DB first. Add `play_by_play` to the adapter and the `shot_event` table.
4. Migration for the `analytics` schema, role setup script, integrity triggers, tests.
5. Modelling doc and first model version, backtested on 2025-26 (Seattle and Vancouver were new teams that year).
6. Web pages and track record.
7. Restore test of an off-site backup, which remains an open launch item and matters more once predictions are public.

## Open questions
- Which weekday should the weekly run publish? (Suggested: after Sunday's games, so Monday morning shows the week ahead.)
- Should a published prediction ever be hidden for a game postponed or moved? (Suggested: keep it, mark the game as postponed.)
- Is the 2026-27 season the first public season for the track record, or should backtested 2025-26 predictions appear, clearly labelled as backtests? Suggested: keep backtests visible but separate, so they cannot be confused with live predictions.
