import hashlib
import logging
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow import notify
from undertow.config import get_settings
from undertow.data import get_session
from undertow.data.session import session_factory
from undertow.fantasy import auth, key_dates, onboarding, waitlist
from undertow.web import accounts, draft_preview, security
from undertow.web import preview_data as sample
from undertow.web.ratelimit import RateLimiter

WEB_DIR = Path(__file__).parent


def nav_context(request: Request) -> dict:
    """The signed-in member for the header's Log in / account control, on every page.

    Costs one lookup, and only for visitors who send a session cookie.
    """
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        return {"nav_user": None}
    try:
        with session_factory()() as session:
            return {"nav_user": auth.user_for_token(session, token)}
    except Exception:
        logger.warning("Could not look up the signed-in member for the header", exc_info=True)
        return {"nav_user": None}


templates = Jinja2Templates(directory=WEB_DIR / "templates", context_processors=[nav_context])


def static_version() -> str:
    """Short hash of every static file, appended to asset URLs (`?v=`) so a changed file is
    always fetched fresh and never served from a browser's stale cache after a deploy."""
    h = hashlib.sha256()
    static = WEB_DIR / "static"
    for path in sorted(static.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(static).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["v"] = static_version()
templates.env.globals["dates"] = key_dates.DATES
templates.env.globals["key_dates"] = key_dates.KEY_DATES

logger = logging.getLogger(__name__)
MAX_FORM_BYTES = 8192
UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign")


def client_key(request: Request) -> str:
    # Railway puts the real client address first in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app() -> FastAPI:
    settings = get_settings()
    accounts_on = settings.accounts_enabled or not settings.is_prod
    if accounts_on and settings.is_prod and settings.secret_key == "dev-only-change-me":
        raise RuntimeError("SECRET_KEY must be set to a long random value before accounts open.")
    templates.env.globals["accounts_enabled"] = accounts_on
    app = FastAPI(title=settings.app_name, docs_url=None if settings.is_prod else "/docs")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    signup_limiter = RateLimiter(max_events=5, window_seconds=600)

    def welcome(session: Session, email: str, first_name: str | None) -> None:
        """Account and set-password email for a new signup. A failure here must never break the
        signup itself (they are already on the list), so it is logged and swallowed."""
        try:
            onboarding.welcome_signup(session, settings, email=email, first_name=first_name)
        except Exception:
            session.rollback()
            logger.exception("Could not create an account for a new waitlist signup")

    def landing(request: Request, form=None, errors=None, status_code: int = 200) -> Response:
        utm = {k: request.query_params.get(k, "")[:100] for k in UTM_FIELDS}
        if form:
            utm = {k: form.get(k, "") for k in UTM_FIELDS}
        context = {
            "app_name": settings.app_name,
            "experience_choices": waitlist.EXPERIENCE_CHOICES,
            "form": form or {},
            "errors": errors or {},
            "utm": utm,
        }
        return templates.TemplateResponse(request, "index.html", context, status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        return landing(request)

    @app.post("/signup")
    async def signup(request: Request, session: Session = Depends(get_session)) -> Response:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return Response(status_code=413)
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        if not signup_limiter.allow(client_key(request)):
            return landing(
                request,
                form,
                {"form": "Too many attempts. Please wait a few minutes and try again."},
                status_code=429,
            )
        # Hidden field real people never fill in. Pretend it worked so bots learn nothing.
        if form.get("website"):
            return RedirectResponse("/thanks", status_code=303)
        data, errors = waitlist.clean_signup(form)
        if data is None:
            return landing(request, form, errors, status_code=422)
        created = await run_in_threadpool(waitlist.add_signup, session, data)
        if created:
            await run_in_threadpool(notify.send_signup_alert, settings, data.email, data.first_name)
            if accounts_on:
                await run_in_threadpool(welcome, session, data.email, data.first_name)
        # Same answer whether or not the email was already on the list.
        return RedirectResponse("/thanks", status_code=303)

    @app.get("/thanks", response_class=HTMLResponse)
    def thanks(request: Request) -> Response:
        return templates.TemplateResponse(request, "thanks.html", {"app_name": settings.app_name})

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request) -> Response:
        return templates.TemplateResponse(request, "about.html", {"app_name": settings.app_name})

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> Response:
        context = {"app_name": settings.app_name, "contact_email": settings.contact_email}
        return templates.TemplateResponse(request, "privacy.html", context)

    @app.get("/fantasy-101", response_class=HTMLResponse)
    def fantasy_101(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "fantasy101.html", {"app_name": settings.app_name}
        )

    if accounts_on:
        app.include_router(accounts.router)

    if settings.preview_enabled or not settings.is_prod:
        # Design mockups on sample data (see preview_data.py). Replaced by real pages later.
        app.include_router(draft_preview.router)

        def preview(request: Request, template: str, tab: str, **extra) -> Response:
            context = {"league": sample.LEAGUE, "active": tab, "app_name": settings.app_name}
            return templates.TemplateResponse(request, f"app/{template}.html", context | extra)

        def matchup_page(request: Request, n: int) -> Response:
            if not 1 <= n <= len(sample.MATCHUPS):
                raise HTTPException(status_code=404)
            m = sample.MATCHUPS[n - 1]
            a, b = sample.TEAMS[m["a"]], sample.TEAMS[m["b"]]
            mine = sample.MY_SLUG in (m["a"], m["b"])
            return preview(request, "matchup", "matchup", a=a, b=b, n=n, mine=mine)

        @app.get("/preview/matchup", response_class=HTMLResponse)
        def preview_matchup(request: Request) -> Response:
            return matchup_page(request, 1)

        @app.get("/preview/matchup/{n}", response_class=HTMLResponse)
        def preview_matchup_n(request: Request, n: int) -> Response:
            return matchup_page(request, n)

        @app.get("/preview/team", response_class=HTMLResponse)
        def preview_team(request: Request) -> Response:
            team = sample.TEAMS[sample.MY_SLUG]
            return preview(
                request,
                "team",
                "team",
                team=team,
                lineup=team["lineup"],
                bench=team["bench"],
                ir=sample.IR,
            )

        @app.get("/preview/players", response_class=HTMLResponse)
        def preview_players(request: Request) -> Response:
            # Trending first: that is the default sort the page opens with.
            rows = sorted(sample.PLAYERS, key=lambda r: -r[7])
            return preview(request, "players", "players", players=rows)

        @app.get("/preview/player/{pid}", response_class=HTMLResponse)
        def preview_player(request: Request, pid: int) -> Response:
            page = sample.PLAYER_PAGES.get(str(pid))
            if page is None:
                raise HTTPException(status_code=404)
            return preview(request, "player", "team", player=page)

        @app.get("/preview/league", response_class=HTMLResponse)
        def preview_league(request: Request) -> Response:
            rows = [
                {"n": m["id"], "a": sample.TEAMS[m["a"]], "b": sample.TEAMS[m["b"]]}
                for m in sample.MATCHUPS
            ]
            return preview(
                request,
                "league",
                "league",
                standings=sample.STANDINGS,
                matchups=rows,
                activity=sample.ACTIVITY,
            )

    @app.get("/healthz")
    def healthz(response: Response, session: Session = Depends(get_session)) -> dict:
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            return {"status": "error", "db": False}
        return {"status": "ok", "db": True}

    return app


app = create_app()
