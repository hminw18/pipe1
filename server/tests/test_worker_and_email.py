from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pipe1_server.email.delivery import EmailMessage, SendGridSender
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


def test_sendgrid_payload_disables_tracking_and_escapes_html() -> None:
    sender = SendGridSender("key", "Pipe1 <no-reply@pipe1.example>")
    payload = sender.payload(
        EmailMessage(
            to="a@example.com",
            template="invitation",
            data={"organization_name": "<script>x</script>", "link": "https://app/invite#token=t"},
        )
    )
    tracking = payload["tracking_settings"]
    assert tracking["click_tracking"]["enable"] is False
    assert tracking["open_tracking"]["enable"] is False
    assert payload["from"] == {"email": "no-reply@pipe1.example", "name": "Pipe1"}
    html = payload["content"][1]["value"]
    assert "<script>" not in html and "&lt;script&gt;" in html


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
    ):
        rendered = render(template, data)
        assert rendered.subject.startswith("[Pipe1]")


def test_unknown_template_rejected() -> None:
    with pytest.raises(ValueError):
        EmailMessage(to="a@example.com", template="free_text")


def test_mask_email() -> None:
    assert mask_email("alice@example.com") == "al***@example.com"
    assert "alice" not in mask_email("alice@example.com")
