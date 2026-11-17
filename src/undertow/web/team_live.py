"""The real Team tab: a team's lineup for a fantasy week, and moving players between slots.

Every rule is in `fantasy.lineups`; this module checks who is asking, turns errors into messages
and renders. The move request is JSON with the CSRF token in the `X-CSRF-Token` header.
"""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data import get_session
from undertow.data.models import FantasyTeam, User
from undertow.fantasy import leagues, lineups, team_view
from undertow.web import security
from undertow.web.accounts import page
from undertow.web.draft_live import member_league
from undertow.web.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter()
move_limiter = RateLimiter(max_events=60, window_seconds=60)
MAX_JSON_BYTES = 2_000


def tabs_for(league_id: int) -> list[tuple[str, str, str]]:
    """The tabs a signed-in member sees in a league. More are added as the pages are built."""
    return [
        ("team", "Team", f"/leagues/{league_id}/team"),
        ("draft", "Draft", f"/leagues/{league_id}/draft"),
        ("stats", "Stats", "/stats"),
        ("league", "League", f"/leagues/{league_id}"),
    ]


def _week_number(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _render(
    request: Request, session: Session, user: User, league_id: int, team_id: int, week: str | None
) -> Response:
    league = member_league(session, user, league_id)
    team = session.get(FantasyTeam, team_id)
    if team is None or team.league_id != league_id:
        raise HTTPException(status_code=404)
    view = team_view.team_page(session, league, team, user, week_no=_week_number(week))
    rows = [*view.starters, *view.bench, *view.ir]
    data = {
        "league_id": league_id,
        "team_id": team_id,
        "labels": view.slot_labels,
        "moves": {r.player_id: r.moves for r in rows if r.moves},
        "names": {r.player_id: r.name for r in rows if r.player_id},
    }
    response = page(
        request,
        "team_live.html",
        user=user,
        league=league,
        view=view,
        data=data,
        tabs=tabs_for(league_id),
        active="team",
        mine=team.owner_id == user.id,
        now=datetime.now(UTC),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/leagues/{league_id}/team", response_class=HTMLResponse)
async def my_team(
    request: Request,
    league_id: int,
    week: str | None = None,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    def find() -> int | None:
        member_league(session, user, league_id)
        mine = session.scalar(
            select(FantasyTeam.id).where(
                FantasyTeam.league_id == league_id, FantasyTeam.owner_id == user.id
            )
        )
        if mine is None and user.is_admin:  # staff who are not in the league look at the first team
            mine = session.scalar(
                select(FantasyTeam.id)
                .where(FantasyTeam.league_id == league_id)
                .order_by(FantasyTeam.id)
            )
        return mine

    team_id = await run_in_threadpool(find)
    if team_id is None:
        return RedirectResponse(f"/leagues/{league_id}", status_code=303)
    return await run_in_threadpool(_render, request, session, user, league_id, team_id, week)


@router.get("/leagues/{league_id}/team/{team_id}", response_class=HTMLResponse)
async def team_by_id(
    request: Request,
    league_id: int,
    team_id: int,
    week: str | None = None,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    return await run_in_threadpool(_render, request, session, user, league_id, team_id, week)


@router.post("/leagues/{league_id}/team/move")
async def move(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    if not security.csrf_ok(request, request.headers.get("x-csrf-token", "")):
        raise HTTPException(status_code=403, detail="Your session expired. Reload and try again.")
    body = await request.body()
    if len(body) > MAX_JSON_BYTES:
        raise HTTPException(status_code=413)
    try:
        data = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=422) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422)
    await run_in_threadpool(member_league, session, user, league_id)
    if not move_limiter.allow(f"user:{user.id}"):
        return JSONResponse({"error": "Slow down a little."}, status_code=429)

    def whole(key: str, required: bool = True) -> int | None:
        value = data.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(key)
        return value

    to_slot = data.get("to_slot")
    try:
        team_id, player_id, swap_with = (
            whole("team_id"),
            whole("player_id"),
            whole("swap_with", False),
        )
    except ValueError:
        return JSONResponse({"error": "Bad request."}, status_code=422)
    if not isinstance(to_slot, str):
        return JSONResponse({"error": "Bad request."}, status_code=422)
    team = await run_in_threadpool(session.get, FantasyTeam, team_id)
    if team is None or team.league_id != league_id:
        raise HTTPException(status_code=404)
    try:
        await run_in_threadpool(
            lambda: lineups.move_player(
                session, user, team_id, player_id, to_slot, swap_with=swap_with
            )
        )
    except leagues.PermissionDenied as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except leagues.LeagueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True})
