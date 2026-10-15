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


def _capture():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "abc"})

    return calls, httpx.Client(transport=httpx.MockTransport(handler))


def test_welcome_email_contains_the_link_and_goes_to_the_member():
    calls, client = _capture()
    settings = _settings(base_url="https://undertowsports.com/")
    sent = notify.send_welcome_email(
        settings, "fan@example.com", "Sam", "tok123", 14, client=client
    )
    assert sent is True
    body = json.loads(calls[0].content)
    assert body["to"] == ["fan@example.com"]
    assert "Hi Sam," in body["text"]
    assert "https://undertowsports.com/account/set-password?token=tok123" in body["text"]
    assert "14 days" in body["text"]


def test_reset_email_says_it_works_once():
    calls, client = _capture()
    sent = notify.send_password_reset_email(
        _settings(), "fan@example.com", None, "tok", 2, client=client
    )
    assert sent is True
    body = json.loads(calls[0].content)
    assert body["text"].startswith("Hi,")
    assert "2 hours" in body["text"] and "only once" in body["text"]
    assert "?token=tok" in body["text"]


def test_member_emails_send_nothing_without_an_email_key():
    calls, client = _capture()
    settings = Settings(email_api_key="")
    assert not notify.send_welcome_email(settings, "a@b.co", "A", "t", 14, client=client)
    assert not notify.send_password_reset_email(settings, "a@b.co", "A", "t", 2, client=client)
    assert calls == []


def test_member_email_failure_does_not_raise():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert notify.send_welcome_email(_settings(), "a@b.co", "A", "t", 14, client=client) is False
