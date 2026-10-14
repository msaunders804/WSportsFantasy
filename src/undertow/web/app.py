from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.db import get_session
from undertow.services import waitlist
from undertow.web.ratelimit import RateLimiter

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

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
    app = FastAPI(title=settings.app_name, docs_url=None if settings.is_prod else "/docs")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    signup_limiter = RateLimiter(max_events=5, window_seconds=600)

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
        await run_in_threadpool(waitlist.add_signup, session, data)
        # Same answer whether or not the email was already on the list.
        return RedirectResponse("/thanks", status_code=303)

    @app.get("/thanks", response_class=HTMLResponse)
    def thanks(request: Request) -> Response:
        return templates.TemplateResponse(request, "thanks.html", {"app_name": settings.app_name})

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> Response:
        context = {"app_name": settings.app_name, "contact_email": settings.contact_email}
        return templates.TemplateResponse(request, "privacy.html", context)

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
