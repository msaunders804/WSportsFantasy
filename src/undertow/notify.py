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


def send_welcome_email(
    settings: Settings,
    to: str,
    first_name: str | None,
    token: str,
    days: int,
    client: httpx.Client | None = None,
) -> bool:
    """Tell a new waitlist member their account exists and how to set a password.

    Best-effort and never raises. Without an email key it sends nothing (and in dev it logs the
    link so the flow can be tried without a mail provider).
    """
    link = set_password_url(settings, token)
    if not settings.email_api_key:
        if not settings.is_prod:
            logger.info("Welcome link for %s (email not configured): %s", to, link)
        return False
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    text = (
        f"{greeting}\n\n"
        "Thanks for joining the Undertow Fantasy list. We made you an account so you can follow "
        "your league and see updates as they happen.\n\n"
        f"Choose a password to finish setting it up (this link works for {days} days):\n{link}\n\n"
        "If the link has expired, you can ask for a new one on the log in page.\n"
        "If you didn't sign up, you can ignore this email and nothing will happen.\n\n"
        "Undertow Fantasy is an independent fan project, not affiliated with the PWHL."
    )
    return _send(
        settings,
        to,
        "Set your Undertow Fantasy password",
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
