# Undertow Sports (PWHL fantasy): conventions

Source of truth for scope and rules is [Plan.MD](Plan.MD); design decisions and their reasons go in [docs/decisions.md](docs/decisions.md). Feed behaviour is in [docs/feed.md](docs/feed.md).

## Commands
- Dev DB: `docker compose -f docker-compose.dev.yml up -d --wait` (Postgres on localhost:5433)
- Install: `uv sync`
- Migrate: `uv run alembic upgrade head`
- Web: `uv run uvicorn undertow.web.app:app --reload`
- Worker: `uv run python -m undertow.worker.main`
- Test: `DATABASE_URL=postgresql+psycopg://undertow:undertow@localhost:5433/undertow uv run pytest` (tests marked `db` skip without `DATABASE_URL`)
- Lint: `uv run ruff check . && uv run ruff format .`

## Layout
- `src/undertow/config.py`: settings from env. `db/`: engine, session, Base.
- `ingest/`: `feed.py` (the only HockeyTech access; live and fixture feeds), `parsing.py` (pure JSON to DTO), `dto.py`, `store.py` (snapshots and upserts), `sync.py` (sync jobs).
- `models/`: SQLAlchemy tables (real-hockey tables so far). `data/`: small config data (conferences).
- `scoring/`: pure scoring engine (exact fractions). `services/`: business logic with no HTTP; `stat_lines.py` bridges DB rows to scoring input.
- `web/`: FastAPI app and templates. `worker/`: scheduler process.
- `migrations/`: Alembic. `tests/`: `ingest/`, `scoring/`, fixtures in `tests/fixtures/hockeytech/`.
- Later phases add fantasy tables, drafts, matchups and waivers under `models/` and `services/`.

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
