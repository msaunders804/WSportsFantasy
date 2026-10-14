# Undertow Sports

PWHL fantasy leagues: two independent 10-team leagues, head-to-head points scoring, live snake draft. Python, FastAPI + Jinja2 + HTMX, PostgreSQL, hosted on Railway.

## Docs
- [Plan.MD](Plan.MD): scope, rules, architecture and build phases (source of truth)
- [docs/decisions.md](docs/decisions.md): each design decision and why
- [docs/feed.md](docs/feed.md): what the unofficial HockeyTech feed provides
- [TODO_MANUAL.md](TODO_MANUAL.md): things only the owner can do
- [CLAUDE.md](CLAUDE.md): developer commands and conventions

## Status
| Phase | State |
|---|---|
| 0 Feed discovery | Done |
| 1 Skeleton | Built (PR #1); CI file pending a token scope |
| 2 Ingestion and scoring engine | Complete (PR #2): adapter, tables, worker jobs, scoring engine, and a clean replay of all 120 games of 2025-26 |
| 3 onward | Not started |

## Quick start
```
docker compose -f docker-compose.dev.yml up -d --wait   # Postgres on localhost:5433
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn undertow.web.app:app --reload             # web
uv run python -m undertow.worker.main                    # worker
DATABASE_URL=postgresql+psycopg://undertow:undertow@localhost:5433/undertow uv run pytest
```
