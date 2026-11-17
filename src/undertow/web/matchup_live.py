"""The real Matchup and League (standings) tabs. Read-only; all numbers come from the fantasy
package. Visible to league members and staff; anyone else gets a 404."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from undertow.data import get_session
from undertow.data.models import Matchup, User
from undertow.fantasy import matchup_view
from undertow.web import security
from undertow.web.accounts import page
from undertow.web.draft_live import member_league
from undertow.web.nav import league_tabs

router = APIRouter()


def _week_number(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _render_matchup(
    request: Request,
    session: Session,
    user: User,
    league_id: int,
    week: str | None,
    matchup_id: int | None,
) -> Response:
    league = member_league(session, user, league_id)
    now = datetime.now(UTC)
    if matchup_id is not None:
        matchup = session.get(Matchup, matchup_id)
        if matchup is None or matchup.league_id != league_id:
            raise HTTPException(status_code=404)
    else:
        matchup = matchup_view.pick_matchup(session, league, user, _week_number(week), now)
    view = matchup_view.matchup_page(session, league, matchup, user, now=now) if matchup else None
    response = page(
        request,
        "matchup_live.html",
        user=user,
        league=league,
        view=view,
        tabs=league_tabs(league),
        active="matchup",
        refresh=30 if view and view.status == "live" else 0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/leagues/{league_id}/matchup", response_class=HTMLResponse)
async def my_matchup(
    request: Request,
    league_id: int,
    week: str | None = None,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    return await run_in_threadpool(_render_matchup, request, session, user, league_id, week, None)


@router.get("/leagues/{league_id}/matchup/{matchup_id}", response_class=HTMLResponse)
async def matchup_by_id(
    request: Request,
    league_id: int,
    matchup_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    return await run_in_threadpool(
        _render_matchup, request, session, user, league_id, None, matchup_id
    )


@router.get("/leagues/{league_id}/standings", response_class=HTMLResponse)
async def standings_tab(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    def build() -> Response:
        league = member_league(session, user, league_id)
        view = matchup_view.standings_page(session, league, user)
        response = page(
            request,
            "standings_live.html",
            user=user,
            league=league,
            view=view,
            tabs=league_tabs(league),
            active="league",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return await run_in_threadpool(build)
