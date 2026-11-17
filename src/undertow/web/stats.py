"""Public stats pages: last season's numbers and the fantasy points they would have scored.

No login needed. The data is the same box scores the app already stores, scored with the standard
table. Pages are cached for a few minutes (the numbers only change when a game is corrected).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.data import get_session
from undertow.fantasy import season_stats as ss
from undertow.fantasy.scoring import ScoringConfig

router = APIRouter()
CACHE = "public, max-age=300"


def render(request: Request, template: str, **context) -> Response:
    from undertow.web.app import templates  # the app's shared Jinja environment

    response = templates.TemplateResponse(
        request, template, {"app_name": get_settings().app_name, **context}
    )
    response.headers["Cache-Control"] = CACHE
    return response


def scoring_rules(config: ScoringConfig) -> dict[str, list[tuple[str, str]]]:
    """The scoring table as (label, points) pairs, for showing above the stats."""
    return {
        "Skaters": [
            ("Goal", ss.plain(config.goal)),
            ("Assist", ss.plain(config.assist)),
            ("Power-play point (bonus)", ss.plain(config.pp_point)),
            ("Short-handed point (bonus)", ss.plain(config.sh_point)),
            ("Shot on goal", ss.plain(config.shot)),
            ("Hit", ss.plain(config.hit)),
            ("Blocked shot", ss.plain(config.block)),
        ],
        "Goalies": [
            ("Regulation win", ss.plain(config.win)),
            ("OT or shootout win", ss.plain(config.win * config.ot_win_multiplier)),
            ("Save", ss.plain(config.save)),
            ("Goal against", ss.plain(config.goal_against)),
            ("Shutout", ss.plain(config.shutout)),
            ("OT or shootout loss", ss.plain(config.ot_loss)),
        ],
    }


def pick_season(session: Session, requested: str | None) -> tuple[int, str, list[tuple[int, str]]]:
    seasons = ss.regular_seasons_with_stats(session)
    if not seasons:
        raise HTTPException(status_code=404)
    if requested is None or requested == "":
        return seasons[0][0], seasons[0][1], seasons
    try:
        wanted = int(requested)
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    for season_id, name in seasons:
        if season_id == wanted:
            return season_id, name, seasons
    raise HTTPException(status_code=404)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request, season: str | None = None, session: Session = Depends(get_session)
) -> Response:
    def build():
        try:
            season_id, name, seasons = pick_season(session, season)
        except HTTPException:
            if season in (None, ""):
                return None  # nothing loaded yet: show the empty page, not an error
            raise
        return ss.season_stats(session, season_id), name, seasons

    built = await run_in_threadpool(build)
    if built is None:
        return render(request, "stats.html", data=None, seasons=[], rules=scoring_rules(ss.SCORING))
    data, _name, seasons = built
    return render(
        request, "stats.html", data=data, seasons=seasons, rules=scoring_rules(ss.SCORING)
    )


@router.get("/stats/player/{player_id}", response_class=HTMLResponse)
async def player_page(
    request: Request,
    player_id: int,
    season: str | None = None,
    session: Session = Depends(get_session),
) -> Response:
    def build():
        season_id, _name, _seasons = pick_season(session, season)
        data = ss.season_stats(session, season_id)
        summary = ss.player_summary(data, player_id)
        if summary is None:
            raise HTTPException(status_code=404)
        overall, at_pos = ss.rank_of(data, summary)
        return data, summary, overall, at_pos, ss.game_log(session, player_id, season_id)

    data, summary, overall, at_pos, log = await run_in_threadpool(build)
    goalie = summary.pos == "G"
    labels = ss.GOALIE_LABELS if goalie else ss.SKATER_LABELS
    breakdown = [
        (label, summary.breakdown[key]) for key, label in labels.items() if key in summary.breakdown
    ]
    return render(
        request,
        "stats_player.html",
        data=data,
        p=summary,
        goalie=goalie,
        overall=overall,
        at_pos=at_pos,
        total_players=len(data.skaters) + len(data.goalies),
        breakdown=breakdown,
        log=log,
        labels=labels,
        columns=list(log[0].stats) if log else [],
    )
