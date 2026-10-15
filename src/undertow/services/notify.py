"""Email alerts to the Undertow team. Currently: a new waitlist signup. Sends through Resend."""

import logging

import httpx

from undertow.config import Settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


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
    owns_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.email_api_key}"},
            json={
                "from": settings.email_from,
                "to": [settings.alert_email],
                "subject": "New Undertow Fantasy signup",
                "text": f"{who} just joined the Undertow Fantasy waitlist.",
            },
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.warning("Failed to send signup alert email", exc_info=True)
        return False
    finally:
        if owns_client:
            client.close()
