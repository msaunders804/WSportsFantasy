"""Pages for accounts and leagues: register, log in, join by invite link, league setup."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.data import get_session
from undertow.data.models import FantasyTeam, League, LeagueMember, LeagueSettings, Season, User
from undertow.fantasy import auth, league_rules, leagues
from undertow.fantasy.league_rules import INT_LIMITS
from undertow.web import security
from undertow.web.ratelimit import RateLimiter

router = APIRouter()
login_ip_limiter = RateLimiter(max_events=20, window_seconds=600)
login_email_limiter = RateLimiter(max_events=5, window_seconds=600)
register_limiter = RateLimiter(max_events=5, window_seconds=600)
join_limiter = RateLimiter(max_events=20, window_seconds=600)

SETTING_LABELS = {
    "roster_f": "Forward slots",
    "roster_d": "Defender slots",
    "roster_util": "Flex (F or D) slots",
    "roster_g": "Goalie slots",
    "roster_bench": "Bench slots",
    "roster_ir": "Injured reserve slots",
    "max_goalies": "Most goalies on a roster",
    "waiver_hours": "Waiver period (hours)",
    "trade_review_hours": "Trade review (hours)",
    "trade_deadline_week": "Trade deadline (end of week)",
    "regular_season_weeks": "Regular season weeks",
    "playoff_teams": "Teams in the playoffs",
}


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def page(request: Request, template: str, status_code: int = 200, **context) -> Response:
    """Render a template with the CSRF token and app name filled in, and set the CSRF cookie."""
    from undertow.web.app import templates  # the app's shared Jinja environment

    settings = get_settings()
    context = {
        "app_name": settings.app_name,
        "csrf_token": security.csrf_token(request),
        **context,
    }
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    security.attach_csrf_cookie(request, response)
    return response


def redirect(url: str, user_token: str | None = None) -> Response:
    response = RedirectResponse(url, status_code=303)
    if user_token:
        security.set_cookie(
            response, security.SESSION_COOKIE, user_token, max_age=auth.SESSION_DAYS * 86400
        )
    return response


def check_csrf(request: Request, form: dict[str, str]) -> None:
    if not security.csrf_ok(request, form.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Your session expired. Reload and try again.")


# ---- accounts ----


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, user: User | None = Depends(security.optional_user)):
    if user:
        return redirect("/leagues")
    return page(
        request,
        "account/register.html",
        form={},
        errors={},
        next=request.query_params.get("next", ""),
    )


@router.post("/register")
async def register(request: Request, session: Session = Depends(get_session)) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    next_url = security.safe_next(form.get("next", ""))
    if not register_limiter.allow(client_key(request)):
        return page(
            request,
            "account/register.html",
            429,
            form=form,
            next=next_url,
            errors={"form": "Too many attempts. Please wait a few minutes."},
        )
    result = await run_in_threadpool(
        auth.register_user,
        session,
        email=form.get("email", ""),
        display_name=form.get("display_name", ""),
        password=form.get("password", ""),
    )
    if result.user is None:
        return page(
            request, "account/register.html", 422, form=form, errors=result.errors, next=next_url
        )
    token = await run_in_threadpool(auth.create_session, session, result.user)
    return redirect(next_url, token)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(security.optional_user)):
    if user:
        return redirect("/leagues")
    return page(
        request, "account/login.html", form={}, errors={}, next=request.query_params.get("next", "")
    )


@router.post("/login")
async def login(request: Request, session: Session = Depends(get_session)) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    next_url = security.safe_next(form.get("next", ""))
    email = auth.normalize_email(form.get("email", ""))
    if not (login_ip_limiter.allow(client_key(request)) and login_email_limiter.allow(email)):
        return page(
            request,
            "account/login.html",
            429,
            form=form,
            next=next_url,
            errors={"form": "Too many attempts. Please wait a few minutes."},
        )
    user = await run_in_threadpool(auth.authenticate, session, email, form.get("password", ""))
    if user is None:
        return page(
            request,
            "account/login.html",
            401,
            form=form,
            next=next_url,
            errors={"form": "That email and password do not match."},
        )
    token = await run_in_threadpool(auth.create_session, session, user)
    return redirect(next_url, token)


@router.post("/logout")
async def logout(request: Request, session: Session = Depends(get_session)) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    token = request.cookies.get(security.SESSION_COOKIE, "")
    if token:
        await run_in_threadpool(auth.end_session, session, token)
    response = redirect("/")
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return response


# ---- leagues ----


@router.get("/leagues", response_class=HTMLResponse)
def my_leagues(
    request: Request,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
):
    return page(
        request, "account/leagues.html", user=user, rows=leagues.leagues_for_user(session, user)
    )


def current_season_id(session: Session) -> int | None:
    return session.scalar(select(Season.id).where(Season.is_current).order_by(Season.id.desc()))


@router.get("/leagues/new", response_class=HTMLResponse)
def new_league_form(request: Request, user: User = Depends(security.require_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403)
    return page(request, "account/league_new.html", user=user, form={"size": "10"}, errors={})


@router.post("/leagues/new")
async def new_league(
    request: Request,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    if not user.is_admin:
        raise HTTPException(status_code=403)
    form = await security.read_form(request)
    check_csrf(request, form)
    season_id = await run_in_threadpool(current_season_id, session)
    try:
        if season_id is None:
            raise leagues.LeagueError("No current season is loaded yet. Run the ingest first.")
        size = int(form.get("size", ""))
        league, _ = await run_in_threadpool(
            leagues.create_league,
            session,
            user,
            name=form.get("name", ""),
            season_id=season_id,
            size=size,
        )
    except ValueError:
        return page(
            request,
            "account/league_new.html",
            422,
            user=user,
            form=form,
            errors={"size": "Enter the number of teams."},
        )
    except leagues.LeagueError as exc:
        return page(
            request, "account/league_new.html", 422, user=user, form=form, errors={"form": str(exc)}
        )
    return redirect(f"/leagues/{league.id}")


def league_context(session: Session, user: User, league_id: int) -> dict:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404)
    try:
        leagues.require_member(session, user, league_id)
    except leagues.PermissionDenied as exc:
        raise HTTPException(status_code=404) from exc  # do not reveal that the league exists
    settings_row = session.get(LeagueSettings, league_id)
    data = leagues.settings_data(settings_row)
    validation = league_rules.validate_settings(
        league.size, data, active_pool=leagues.active_player_pool(session) or None
    )
    teams = session.execute(
        select(FantasyTeam, User.display_name, LeagueMember.role, User.id)
        .join(User, User.id == FantasyTeam.owner_id)
        .join(
            LeagueMember,
            (LeagueMember.league_id == FantasyTeam.league_id) & (LeagueMember.user_id == User.id),
        )
        .where(FantasyTeam.league_id == league_id)
        .order_by(FantasyTeam.created_at)
    ).all()
    manager = leagues.can_manage(session, user, league_id)
    return {
        "user": user,
        "league": league,
        "settings": data,
        "limits": INT_LIMITS,
        "labels": SETTING_LABELS,
        "teams": teams,
        "warnings": validation.warnings,
        "manager": manager,
        "editable": manager and league.status == "setup",
        "invite_url": f"{get_settings().base_url.rstrip('/')}/join/{league.invite_code}"
        if manager
        else "",
        "errors": {},
        "saved": False,
    }


@router.get("/leagues/{league_id}", response_class=HTMLResponse)
def league_page(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
):
    return page(request, "account/league.html", **league_context(session, user, league_id))


@router.post("/leagues/{league_id}/settings")
async def league_settings(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    context = await run_in_threadpool(league_context, session, user, league_id)
    try:
        changes = {name: int(form[name]) for name in INT_LIMITS if name in form}
    except ValueError:
        context["errors"] = {"form": "Every setting must be a whole number."}
        return page(request, "account/league.html", 422, **context)
    try:
        await run_in_threadpool(leagues.update_settings, session, user, league_id, changes)
    except leagues.PermissionDenied as exc:
        raise HTTPException(status_code=403) from exc
    except leagues.LeagueError as exc:
        context["errors"] = {"form": str(exc)}
        return page(request, "account/league.html", 422, **context)
    session.expire_all()
    context = await run_in_threadpool(league_context, session, user, league_id)
    context["saved"] = True
    return page(request, "account/league.html", **context)


@router.post("/leagues/{league_id}/roles")
async def league_roles(
    request: Request,
    league_id: int,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    """Staff only: make a member the commissioner or co-commissioner."""
    form = await security.read_form(request)
    check_csrf(request, form)
    if not user.is_admin:
        raise HTTPException(status_code=403)
    try:
        await run_in_threadpool(
            leagues.set_role,
            session,
            user,
            league_id,
            int(form.get("user_id", "")),
            form.get("role", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422) from exc
    except leagues.LeagueError as exc:
        context = await run_in_threadpool(league_context, session, user, league_id)
        context["errors"] = {"form": str(exc)}
        return page(request, "account/league.html", 422, **context)
    return redirect(f"/leagues/{league_id}")


# ---- joining by invite link ----


@router.get("/join/{code}", response_class=HTMLResponse)
def join_form(
    request: Request,
    code: str,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
):
    league = session.scalar(select(League).where(League.invite_code == code))
    if league is None:
        raise HTTPException(status_code=404)
    return page(
        request, "account/join.html", user=user, league=league, code=code, form={}, errors={}
    )


@router.post("/join/{code}")
async def join(
    request: Request,
    code: str,
    user: User = Depends(security.require_user),
    session: Session = Depends(get_session),
) -> Response:
    form = await security.read_form(request)
    check_csrf(request, form)
    league = await run_in_threadpool(
        lambda: session.scalar(select(League).where(League.invite_code == code))
    )
    if league is None or not join_limiter.allow(client_key(request)):
        raise HTTPException(status_code=404)
    try:
        team = await run_in_threadpool(
            leagues.join_league, session, user, code, form.get("team_name", "")
        )
    except leagues.LeagueError as exc:
        return page(
            request,
            "account/join.html",
            422,
            user=user,
            league=league,
            code=code,
            form=form,
            errors={"form": str(exc)},
        )
    return redirect(f"/leagues/{team.league_id}")
