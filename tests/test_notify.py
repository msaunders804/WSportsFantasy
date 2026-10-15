import json

import httpx

from undertow import notify
from undertow.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(email_api_key="key123", alert_email="me@example.com", **overrides)


def test_send_signup_alert_posts_to_resend():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sent = notify.send_signup_alert(_settings(), "fan@example.com", "Sam", client=client)

    assert sent is True
    assert len(calls) == 1
    req = calls[0]
    assert str(req.url) == notify.RESEND_API_URL
    assert req.headers["authorization"] == "Bearer key123"
    body = json.loads(req.content)
    assert body["to"] == ["me@example.com"]
    assert "Sam (fan@example.com)" in body["text"]


def test_send_signup_alert_without_first_name_uses_email_only():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notify.send_signup_alert(_settings(), "fan@example.com", None, client=client)

    body = json.loads(calls[0].content)
    assert "fan@example.com just joined" in body["text"]


def test_send_signup_alert_disabled_without_config():
    sent = notify.send_signup_alert(Settings(email_api_key="", alert_email=""), "a@b.co", None)
    assert sent is False


def test_send_signup_alert_swallows_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sent = notify.send_signup_alert(_settings(), "fan@example.com", None, client=client)
    assert sent is False
