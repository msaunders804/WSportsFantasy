"""The real draft room: a page, a state endpoint the browser polls, and the actions.

Every rule is in `fantasy.draft_room`; this module only checks who is asking, turns errors into
messages, and returns JSON. JSON requests carry the CSRF token in the `X-CSRF-Token` header.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data import get_session
from undertow.data.models import Draft, FantasyTeam, League, User
from undertow.fantasy import draft_room, draft_view, leagues
from undertow.web import security
from undertow.web.accounts import check_csrf, page, redirect
from undertow.web.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter()
pick_limiter = RateLimiter(max_events=30, window_seconds=60)
MAX_JSON_BYTES = 8_000
CONTROL_ACTIONS = ("pause", "resume", "undo")


def member_league(session: Session, user: User, league_id: int) -> League:
    """The league, or a 404 for anyone who is not in it (so its existence is not revealed)."""
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404)
    try:
        leagues.require_member(session, user, league_id)
    except leagues.PermissionDenied as exc:
        raise HTTPException(status_code=404) from exc
    return league


async def _json_body(request: Request) -> dict:
    if not security.csrf_ok(request, request.headers.get("x-csrf-token", "")):
        raise HTTPException(status_code=403, detail="Your session expired. Reload and try again.")
    body = await request.body()
    if len(body) > MAX_JSON_BYTES:
        raise HTTPException(status_code=413)
    try:
        data = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Bad request.") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Bad request.")
    return data


def _error(message: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _state(session: Session, league: League, dr: Draft, user: User) -> dict:
    session.expire_all()
    return draft_view.state(session, league, dr, user)


@router.get("/leagues/{league_id}/draft", response_class=HTMLResponse)
def draft_page(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    league = member_league(session, user, league_id)
    dr = draft_room.draft_for_league(session, league_id)
    if dr is None:
        teams = session.execute(leagues_teams_query(league_id)).all()
        return page(
            request,
            "draft_lobby.html",
            user=user,
            league=league,
            teams=teams,
            manager=leagues.can_manage(session, user, league_id),
            full=len(teams) >= league.size,
            basis=_ranking_basis(session, league),
            errors={},
            default_seconds=draft_room.DEFAULT_PICK_SECONDS,
            min_seconds=draft_room.MIN_PICK_SECONDS,
            max_seconds=draft_room.MAX_PICK_SECONDS,
        )
    data = draft_view.page_data(session, league, dr, user)
    data["csrf"] = security.csrf_token(request)
    return page(
        request,
        "draft_live.html",
        user=user,
        league=league,
        data=data,
        n_teams=len(dr.order),
        rounds=dr.rounds,
    )


def _ranking_basis(session: Session, league: League) -> str | None:
    return draft_view.cached_basis(session, league.season_id)[1]


def leagues_teams_query(league_id: int):
    return (
        select(FantasyTeam.name, User.display_name)
        .join(User, User.id == FantasyTeam.owner_id)
        .where(FantasyTeam.league_id == league_id)
        .order_by(FantasyTeam.created_at)
    )


@router.post("/leagues/{league_id}/draft/start")
async def draft_start(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    await run_in_threadpool(member_league, session, user, league_id)
    try:
        seconds = int(form.get("pick_seconds", "") or draft_room.DEFAULT_PICK_SECONDS)
        await run_in_threadpool(
            lambda: draft_room.start_draft(session, user, league_id, pick_seconds=seconds)
        )
    except leagues.PermissionDenied as exc:
        raise HTTPException(status_code=403) from exc
    except (ValueError, leagues.LeagueError) as exc:
        message = "Enter the pick timer in seconds." if isinstance(exc, ValueError) else str(exc)
        league = session.get(League, league_id)
        teams = session.execute(leagues_teams_query(league_id)).all()
        return page(
            request,
            "draft_lobby.html",
            422,
            user=user,
            league=league,
            teams=teams,
            manager=True,
            full=len(teams) >= league.size,
            basis=_ranking_basis(session, league),
            errors={"form": message},
            default_seconds=draft_room.DEFAULT_PICK_SECONDS,
            min_seconds=draft_room.MIN_PICK_SECONDS,
            max_seconds=draft_room.MAX_PICK_SECONDS,
        )
    return redirect(f"/leagues/{league_id}/draft")


@router.get("/leagues/{league_id}/draft/state")
def draft_state(
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    league = member_league(session, user, league_id)
    dr = draft_room.draft_for_league(session, league_id)
    if dr is None:
        raise HTTPException(status_code=404)
    response = JSONResponse(draft_view.state(session, league, dr, user))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/leagues/{league_id}/draft/pick")
async def draft_pick(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    data = await _json_body(request)
    league = await run_in_threadpool(member_league, session, user, league_id)
    dr = await run_in_threadpool(draft_room.draft_for_league, session, league_id)
    if dr is None:
        raise HTTPException(status_code=404)
    if not pick_limiter.allow(f"user:{user.id}"):
        return _error("Slow down a little.", 429)
    player_id = data.get("player_id")
    if not isinstance(player_id, int) or isinstance(player_id, bool):
        return _error("Choose a player.", 422)
    try:
        await run_in_threadpool(draft_room.make_pick, session, user, dr.id, player_id)
    except draft_room.DraftError as exc:
        # The pick may have lost a race; hand back the current state along with the reason.
        current = await run_in_threadpool(_state, session, league, dr, user)
        return JSONResponse({"error": str(exc), "state": current}, status_code=409)
    return JSONResponse({"state": await run_in_threadpool(_state, session, league, dr, user)})


@router.post("/leagues/{league_id}/draft/queue")
async def draft_queue(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    data = await _json_body(request)
    dr = await run_in_threadpool(draft_room.draft_for_league, session, league_id)
    if dr is None:
        raise HTTPException(status_code=404)
    ids = data.get("player_ids")
    if not isinstance(ids, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in ids
    ):
        return _error("Bad queue.", 422)
    try:
        queue = await run_in_threadpool(draft_room.set_queue, session, user, dr.id, ids)
    except draft_room.DraftError as exc:
        return _error(str(exc))
    return JSONResponse({"queue": queue})


@router.post("/leagues/{league_id}/draft/control")
async def draft_control(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    """Commissioner (or staff): pause, resume, or take back the last pick."""
    data = await _json_body(request)
    league = await run_in_threadpool(member_league, session, user, league_id)
    dr = await run_in_threadpool(draft_room.draft_for_league, session, league_id)
    if dr is None:
        raise HTTPException(status_code=404)
    action = data.get("action")
    if action not in CONTROL_ACTIONS:
        return _error("Unknown action.", 422)
    fn = {
        "pause": draft_room.pause,
        "resume": draft_room.resume,
        "undo": draft_room.undo_last_pick,
    }[action]
    try:
        await run_in_threadpool(fn, session, user, dr.id)
    except leagues.PermissionDenied as exc:
        raise HTTPException(status_code=403) from exc
    except draft_room.DraftError as exc:
        return _error(str(exc))
    return JSONResponse({"state": await run_in_threadpool(_state, session, league, dr, user)})
