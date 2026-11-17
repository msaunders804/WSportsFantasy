"""Staff-only overview of every league and how the site is doing. Read-only."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from undertow.data import get_session
from undertow.data.models import User
from undertow.fantasy import admin_view
from undertow.web import security
from undertow.web.accounts import page

router = APIRouter()


def require_staff(user: User = Depends(security.require_user)) -> User:
    """Staff only. Everyone else gets a 404, so the page's existence is not revealed."""
    if not user.is_admin:
        raise HTTPException(status_code=404)
    return user


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> Response:
    rows = await run_in_threadpool(admin_view.league_rows, session)
    totals = await run_in_threadpool(admin_view.summary, session)
    response = page(
        request,
        "admin.html",
        user=user,
        rows=rows,
        totals=totals,
        now=datetime.now(UTC),
        attention=sum(1 for r in rows if r.flags),
    )
    response.headers["Cache-Control"] = "no-store"
    return response
