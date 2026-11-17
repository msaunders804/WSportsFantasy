# Undertow Sports (PWHL fantasy): conventions

Source of truth for scope and rules is [Plan.MD](Plan.MD); design decisions and their reasons go in [docs/decisions.md](docs/decisions.md). Feed behaviour is in [docs/feed.md](docs/feed.md).

## Commands
- Dev DB: `docker compose -f docker-compose.dev.yml up -d --wait` (Postgres on localhost:5433)
- Install: `uv sync`
- Migrate: `uv run alembic upgrade head`
- Web: `uv run uvicorn undertow.web.app:app --reload`
- Worker: `uv run python -m undertow.worker.main`
- Test: `DATABASE_URL=postgresql+psycopg://undertow:undertow@localhost:5433/undertow uv run pytest` (tests marked `db` skip without `DATABASE_URL`). **DB tests TRUNCATE the hockey tables in that database**, so never point them at anything but the throwaway dev DB.
- Season replay: `uv run python -m undertow.ingest.replay --season 8 [--offline]` (caches responses in `data/replay_cache/`, gitignored; first run fetches about 130 responses live and needs `HOCKEYTECH_KEY`)
- Lint: `uv run ruff check . && uv run ruff format .`

## Layout
One repo for everything Undertow: the fantasy game, the data feed, and content work for social media.
- `src/undertow/config.py`: settings from env. `notify.py`: outgoing email (Resend), shared by everything.
- `ingest/`: the HockeyTech API client and the single source of truth for real-hockey data. `feed.py` (the only HockeyTech access; live and fixture feeds), `parsing.py` (pure JSON to DTO), `dto.py`, `store.py` (snapshots and upserts), `sync.py` (sync functions), `jobs.py` (scheduled worker jobs), `replay.py` (season replay and integrity checks).
- `data/`: the data layer. `base.py` and `session.py` (engine, session, Base), `models/` (`hockey.py`, `fantasy.py`, `waitlist.py`), `migrations/` (Alembic), `conferences.json` (small config data).
- `fantasy/`: the game, with no HTTP code. `scoring/` (pure scoring engine, exact fractions), `auth.py` (accounts and sessions), `leagues.py` and `league_rules.py` (leagues, teams, settings), `draft.py`, `waitlist.py`, `onboarding.py` (waitlist signup to account, set-password links, backfill command), `key_dates.py` (the dates every page shows), `home.py` (which home page a member sees), `draft_time.py` (draft start times: parse, show, countdown), `stat_lines.py` (bridges DB rows to scoring input). `season_calendar.py` (fantasy weeks from the real schedule), `schedule.py` (head-to-head schedules), `lineups.py` (slots, moves, per-game locks), `draft_room.py` and `draft_view.py` (the real draft), `rankings.py`, `season_stats.py`, `admin_view.py`.
- `content/`: analytics, stat notes and simulations for social media. Reads the database through `undertow.data`, never the feed directly.
- `web/`: FastAPI routes, templates and static files (`tokens.css`). `worker/`: the scheduler process (kept as `undertow.worker.main` because Railway's start command names it).
- `analysis/` (repo root): notebooks and one-off work such as TikTok pieces. Never deployed (not copied into the Docker image). Anything reusable moves into `content/`.
- `tests/` mirrors the package: `ingest/`, `fantasy/`, `web/`, fixtures in `tests/fixtures/hockeytech/`.
- Dependencies point one way: `web` and `worker` use the other packages; `fantasy`, `content` and `ingest` use `data`; nothing outside `web` imports `web`. The one cross-link is `ingest/replay.py`, which scores replayed games with `fantasy.scoring` to check them.

## Rules (from Plan section 9)
- One phase at a time; a phase is done when its tests pass.
- All HockeyTech access goes through one adapter module behind an interface. Key comes from config (`HOCKEYTECH_KEY`), never in code. Tests replay fixtures in `tests/fixtures/hockeytech/`; no live network in tests.
- Scoring is a pure function `(stat line, scoring config) -> points + breakdown`. No DB access.
- Business rules (roster validation, locks, waivers, schedule generation) live in a service layer with no HTTP or template code.
- Contested operations (draft pick, waiver claim, trade) are a single DB transaction backed by constraints.
- Never hardcode season, team list or stat list. Read from the DB or config.
- Positions are F, D, G only. Map the feed's C/LW/RW/LD/RD in the adapter.
- Store times in UTC. Use exact decimals for points; round only for display.
- Scheduled jobs run only in `worker`, never in `web`.
- Connect to Postgres directly (session mode), never through a transaction pooler, because of LISTEN/NOTIFY and advisory locks.
- Secrets only via environment variables; add new ones to `.env.example`.
- Record each design decision in `docs/decisions.md`.
