from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.db import get_session

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, docs_url=None if settings.is_prod else "/docs")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        return templates.TemplateResponse(request, "index.html", {"app_name": settings.app_name})

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
