"""Outgoing email through Resend: alerts to the Undertow team, and links sent to members."""

import logging

import httpx

from undertow.config import Settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def _send(
    settings: Settings,
    to: str,
    subject: str,
    text: str,
    client: httpx.Client | None,
    failure: str,
) -> bool:
    """Post one plain-text email to Resend. Never raises; returns whether it was sent."""
    owns_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.email_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject, "text": text},
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.warning(failure, exc_info=True)
        return False
    finally:
        if owns_client:
            client.close()


def send_signup_alert(
    settings: Settings,
    email: str,
    first_name: str | None,
    client: httpx.Client | None = None,
) -> bool:
    """Best-effort email to settings.alert_email when someone joins the waitlist.

    Never raises: a failed alert must not break the signup flow for the visitor.
    Returns whether it was actually sent (false if unconfigured, or the request failed).
    """
    if not settings.email_api_key or not settings.alert_email:
        return False
    who = f"{first_name} ({email})" if first_name else email
    return _send(
        settings,
        settings.alert_email,
        "New Undertow Fantasy signup",
        f"{who} just joined the Undertow Fantasy waitlist.",
        client,
        "Failed to send signup alert email",
    )


def set_password_url(settings: Settings, token: str) -> str:
    return f"{settings.base_url.rstrip('/')}/account/set-password?token={token}"


def site_url(settings: Settings, path: str = "/") -> str:
    return f"{settings.base_url.rstrip('/')}{path}"


def send_welcome_email(
    settings: Settings,
    to: str,
    first_name: str | None,
    token: str,
    days: int,
    client: httpx.Client | None = None,
    invites_by: str | None = None,
    draft_window: str | None = None,
) -> bool:
    """Welcome a new waitlist member: their account, the set-password link, and what they can
    do while they wait for a league.

    Best-effort and never raises. Without an email key it sends nothing (and in dev it logs the
    link so the flow can be tried without a mail provider).
    """
    link = set_password_url(settings, token)
    if not settings.email_api_key:
        if not settings.is_prod:
            logger.info("Welcome link for %s (email not configured): %s", to, link)
        return False
    greeting = f"Hi {first_name}!" if first_name else "Hi there!"
    when = []
    if invites_by:
        when.append(f"We'll place you in a league and email your invite by {invites_by}")
    if draft_window:
        when.append(f"drafts run {draft_window}")
    timeline = f"{' and '.join(when)}.\n\n" if when else ""
    text = (
        f"{greeting}\n\n"
        "We are SO excited for the PWHL season to begin, and even more excited to dive into "
        "Fantasy PWHL with you! Thanks for joining the Undertow Fantasy list! We made you an "
        "account so you can follow your league and catch every update as it happens.\n\n"
        f"First, choose a password to finish setting it up (this link works for {days} days):\n"
        f"{link}\n\n"
        f"{timeline}"
        "While you wait for your league, here's what you can do right now!\n"
        f"- Take a practice draft and get a feel for the live draft room: "
        f"{site_url(settings, '/preview/draft')}\n"
        f"- Learn the rules, scoring and key dates in Fantasy 101: "
        f"{site_url(settings, '/fantasy-101')}\n"
        f"- Visit your home page (after you set your password): {site_url(settings, '/home')}\n"
        f"- Meet the people behind Undertow: {site_url(settings, '/about')}\n"
        f"- Know someone who would love this? Send them {site_url(settings)}\n\n"
        "If your link expires, you can ask for a new one on the log in page.\n"
        "If you didn't sign up, you can ignore this email and nothing will happen.\n\n"
        "See you on the ice!\n"
        "The Undertow Fantasy team\n\n"
        "Undertow Fantasy is an independent fan project, not affiliated with the PWHL."
    )
    return _send(
        settings,
        to,
        "Welcome to Undertow Fantasy! Set your password",
        text,
        client,
        "Failed to send welcome email",
    )


def send_password_reset_email(
    settings: Settings,
    to: str,
    first_name: str | None,
    token: str,
    hours: int,
    client: httpx.Client | None = None,
) -> bool:
    """Email a set-password link to someone who asked for one. Best-effort, never raises."""
    link = set_password_url(settings, token)
    if not settings.email_api_key:
        if not settings.is_prod:
            logger.info("Password link for %s (email not configured): %s", to, link)
        return False
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    text = (
        f"{greeting}\n\n"
        f"Use this link to choose a new Undertow Fantasy password (it works for {hours} hours "
        f"and only once):\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email. Your password has not changed."
    )
    return _send(
        settings,
        to,
        "Your Undertow Fantasy password link",
        text,
        client,
        "Failed to send password link email",
    )


def send_league_invite_email(
    settings: Settings,
    to: str,
    inviter_name: str,
    league_name: str,
    code: str,
    client: httpx.Client | None = None,
) -> bool:
    """Tell someone a friend invited them to a private league. Best-effort, never raises.

    Sent only when a commissioner enters the address, once per address per league. The address
    is used for this one email and to recognise the invite when that person joins.
    """
    link = site_url(settings, f"/join/{code}")
    if not settings.email_api_key:
        if not settings.is_prod:
            logger.info("League invite for %s (email not configured): %s", to, link)
        return False
    text = (
        f"Hi,\n\n{inviter_name} invited you to join their Undertow Fantasy league, "
        f'"{league_name}", for the PWHL season.\n\n'
        f"Open this link to join (you'll make a free account first if you don't have one):\n"
        f"{link}\n\n"
        "New to fantasy? No problem. We explain every step: "
        f"{site_url(settings, '/fantasy-101')}\n\n"
        "If you don't know who this is, you can ignore this email. We only send it once and "
        "won't add you to any list.\n\n"
        "The Undertow Fantasy team\n\n"
        "Undertow Fantasy is an independent fan project, not affiliated with the PWHL."
    )
    return _send(
        settings,
        to,
        f"{inviter_name} invited you to {league_name} on Undertow Fantasy",
        text,
        client,
        "Failed to send league invite email",
    )
