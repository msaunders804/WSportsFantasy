"""Cookies, CSRF and form reading shared by the account and league pages.

CSRF uses a double submit: a random `ut_csrf` cookie (HttpOnly, SameSite=Lax) plus a hidden form
field holding an HMAC of that cookie under SECRET_KEY. A forged cross-site POST can carry the
cookie but cannot compute the field. It works the same for logged-in and logged-out forms.
"""

import hashlib
import hmac
import logging
import secrets
from urllib.parse import parse_qs, quote

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.data import get_session
from undertow.data.models import User
from undertow.fantasy import auth

logger = logging.getLogger(__name__)
SESSION_COOKIE = "ut_session"
CSRF_COOKIE = "ut_csrf"
MAX_FORM_BYTES = 8192


def cookie_secure() -> bool:
    settings = get_settings()
    return settings.is_prod or settings.base_url.startswith("https://")


def set_cookie(response: Response, name: str, value: str, max_age: int | None = None) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def _csrf_cookie(request: Request) -> str:
    """This browser's CSRF cookie value, minting one (to be set on the response) if absent."""
    value = request.cookies.get(CSRF_COOKIE)
    if not value:
        value = getattr(request.state, "csrf_cookie", None) or secrets.token_urlsafe(24)
        request.state.csrf_cookie = value
        request.state.csrf_new = True
    return value


def csrf_token(request: Request) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, _csrf_cookie(request).encode(), hashlib.sha256).hexdigest()


def csrf_ok(request: Request, submitted: str) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not submitted:
        return False
    key = get_settings().secret_key.encode()
    expected = hmac.new(key, cookie.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, submitted)


def attach_csrf_cookie(request: Request, response: Response) -> None:
    if getattr(request.state, "csrf_new", False):
        set_cookie(response, CSRF_COOKIE, request.state.csrf_cookie)


async def read_form(request: Request) -> dict[str, str]:
    """Parse a small urlencoded form body. Oversized bodies are refused."""
    body = await request.body()
    if len(body) > MAX_FORM_BYTES:
        raise HTTPException(status_code=413)
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    """The signed-in member, or None. Never raises: if the lookup fails (database down) the
    visitor is treated as signed out, so public pages such as the front page keep loading."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        return auth.user_for_token(session, token)
    except Exception:
        logger.warning("Could not look up the signed-in member", exc_info=True)
        return None


def require_user(request: Request, user: User | None = Depends(optional_user)) -> User:
    """Logged-in user, or a redirect to the login page that returns here afterwards."""
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(
            status_code=303, headers={"Location": f"/login?next={quote(safe_next(target))}"}
        )
    return user


def safe_next(target: str, default: str = "/home") -> str:
    """Only allow same-site paths as a post-login destination (no open redirect)."""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default
