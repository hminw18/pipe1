from __future__ import annotations

import threading
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pipe1_server.email.delivery import (
    BackgroundQueue,
    EmailMessage,
    PermanentEmailError,
    ResendSender,
)
from pipe1_server.email.templates import mask_email, render
from pipe1_server.settings import get_settings
from pipe1_server.worker import create_worker_app, get_sender, get_token_verifier
from tests.api_support import make_settings

WORKER_URL = "https://worker.internal.example"
TASKS_ACCOUNT = "tasks@project.iam.gserviceaccount.com"


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def _worker(claims: dict[str, Any] | Exception):
    app = create_worker_app()
    sender = _RecordingSender()

    def verify(token: str, audience: str) -> dict[str, Any]:
        assert audience == WORKER_URL
        if isinstance(claims, Exception):
            raise claims
        return claims

    settings = make_settings(worker_url=WORKER_URL, tasks_service_account=TASKS_ACCOUNT)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_token_verifier] = lambda: verify
    app.dependency_overrides[get_sender] = lambda: sender
    return TestClient(app), sender


MESSAGE = EmailMessage(to="a@example.com", template="password_changed").to_json()


def test_worker_sends_with_valid_tasks_token() -> None:
    client, sender = _worker({"email": TASKS_ACCOUNT, "email_verified": True})
    response = client.post(
        "/internal/tasks/email", content=MESSAGE, headers={"Authorization": "Bearer t"}
    )
    assert response.status_code == 204
    assert sender.sent[0].to == "a@example.com"


@pytest.mark.parametrize(
    ("claims", "header", "expected"),
    [
        ({"email": TASKS_ACCOUNT, "email_verified": True}, None, 401),
        (ValueError("bad signature"), "Bearer forged", 401),
        (
            {"email": "someone-else@x.iam.gserviceaccount.com", "email_verified": True},
            "Bearer t",
            403,
        ),
        ({"email": TASKS_ACCOUNT, "email_verified": False}, "Bearer t", 403),
    ],
)
def test_worker_rejects_unauthenticated_callers(claims, header, expected) -> None:
    client, sender = _worker(claims)
    headers = {"Authorization": header} if header else {}
    response = client.post("/internal/tasks/email", content=MESSAGE, headers=headers)
    assert response.status_code == expected
    assert sender.sent == []


def test_resend_payload_escapes_html() -> None:
    sender = ResendSender("key", "Pipe1 <no-reply@pipe1.example>")
    payload = sender.payload(
        EmailMessage(
            to="a@example.com",
            template="invitation",
            data={"organization_name": "<script>x</script>", "link": "https://app/invite#token=t"},
        )
    )
    assert payload["from"] == "Pipe1 <no-reply@pipe1.example>"
    assert payload["to"] == ["a@example.com"]
    assert "https://app/invite#token=t" in payload["text"]
    assert "<script>" not in payload["html"] and "&lt;script&gt;" in payload["html"]


def test_every_template_renders() -> None:
    data = {
        "link": "https://x/#token=t",
        "organization_name": "회사",
        "new_email_masked": "ab***@x",
    }
    for template in (
        "invitation",
        "password_reset",
        "password_changed",
        "email_change_confirm",
        "email_changed_notice",
        "mfa_reset_notice",
        "delivery_test",
    ):
        rendered = render(template, data)
        assert rendered.subject.startswith("[Pipe1]")


def test_mask_email() -> None:
    assert mask_email("alice@example.com") == "al***@example.com"
    assert "alice" not in mask_email("alice@example.com")


def _resend(status: int, body: dict[str, Any] | None = None) -> ResendSender:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == ResendSender.API_URL
        assert request.headers["Authorization"] == "Bearer key"
        assert request.headers["User-Agent"]
        return httpx2.Response(status, json=body or {})

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return ResendSender("key", "Pipe1 <no-reply@pipe1.example>", client=client)


PLAIN = EmailMessage(to="a@example.com", template="password_changed")


@pytest.mark.parametrize("status", [400, 422])
def test_resend_rejection_is_permanent(status: int) -> None:
    body = {"statusCode": status, "name": "validation_error", "message": "Invalid `to` field."}
    with pytest.raises(PermanentEmailError, match="Invalid `to` field"):
        _resend(status, body).send(PLAIN)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_resend_transient_or_config_errors_are_retryable(status: int) -> None:
    with pytest.raises(httpx2.HTTPStatusError):
        _resend(status).send(PLAIN)


class _FlakySender:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.sent: list[EmailMessage] = []
        self.attempts = 0

    def send(self, message: EmailMessage) -> None:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)


def test_background_queue_retries_transient_failures() -> None:
    sender = _FlakySender([httpx2.ConnectError("down"), httpx2.ConnectError("down")])
    delays: list[float] = []
    outbox = BackgroundQueue(sender, retry_delays=(1, 2, 3), sleep=delays.append)
    outbox.enqueue(PLAIN)
    outbox.join()
    assert sender.sent == [PLAIN]
    assert delays == [1, 2]


def test_background_queue_does_not_retry_rejected_messages() -> None:
    sender = _FlakySender([PermanentEmailError("bad address")])
    delays: list[float] = []
    outbox = BackgroundQueue(sender, retry_delays=(1, 2), sleep=delays.append)
    outbox.enqueue(PLAIN)
    outbox.join()
    assert sender.attempts == 1 and delays == []


def test_background_queue_does_not_block_the_caller() -> None:
    release = threading.Event()

    class SlowSender:
        def send(self, message: EmailMessage) -> None:
            release.wait(5)

    outbox = BackgroundQueue(SlowSender())
    outbox.enqueue(PLAIN)  # returns while the provider call is still in flight
    release.set()
    outbox.join()


def test_worker_drops_messages_the_provider_rejects() -> None:
    client, sender = _worker({"email": TASKS_ACCOUNT, "email_verified": True})

    def reject(message: EmailMessage) -> None:
        raise PermanentEmailError("bad address")

    sender.send = reject  # type: ignore[method-assign]
    response = client.post(
        "/internal/tasks/email", content=MESSAGE, headers={"Authorization": "Bearer t"}
    )
    assert response.status_code == 204


@pytest.mark.parametrize(
    "overrides",
    [
        {"email_sender": "resend"},
        {"email_sender": "resend", "resend_api_key": ""},
        {"email_sender": "resend", "resend_api_key": "re_x"},  # default From is localhost
        {"email_sender": "sendgrid", "resend_api_key": "re_x"},
        {"email_queue": "thread"},
        {"email_sender": "smtp"},
    ],
)
def test_incomplete_email_settings_fail_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)
