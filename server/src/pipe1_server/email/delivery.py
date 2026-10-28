"""Email queueing and sending (docs/Web_Auth_Spec.md §11).

The API never talks to SendGrid. It enqueues a message after the request's transaction has
committed (FastAPI background task), so provider latency or outages cannot change login or
reset response times. In production the queue is Cloud Tasks and the worker sends.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx2

from pipe1_server.email.templates import TEMPLATES, render
from pipe1_server.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    template: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.template not in TEMPLATES:
            raise ValueError(f"unknown email template: {self.template}")

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> EmailMessage:
        payload = json.loads(raw)
        return cls(to=payload["to"], template=payload["template"], data=payload.get("data", {}))


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class EmailQueue(Protocol):
    def enqueue(self, message: EmailMessage) -> None: ...


class ConsoleSender:
    """Development only: logs the rendered email, including links."""

    def send(self, message: EmailMessage) -> None:
        rendered = render(message.template, message.data)
        logger.warning("EMAIL to=%s subject=%s\n%s", message.to, rendered.subject, rendered.text)


class SendGridSender:
    API_URL = "https://api.sendgrid.com/v3/mail/send"

    def __init__(
        self,
        api_key: str,
        from_address: str,
        *,
        sandbox: bool = False,
        client: httpx2.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._from = from_address
        self._sandbox = sandbox
        self._client = client or httpx2.Client(timeout=10.0)

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        rendered = render(message.template, message.data)
        name, _, address = self._from.rpartition(" <")
        sender = {"email": address.rstrip(">"), "name": name} if address else {"email": self._from}
        return {
            "personalizations": [{"to": [{"email": message.to}]}],
            "from": sender,
            "subject": rendered.subject,
            "content": [
                {"type": "text/plain", "value": rendered.text},
                {"type": "text/html", "value": rendered.html},
            ],
            # Token links must not be rewritten through SendGrid's tracking domain.
            "tracking_settings": {
                "click_tracking": {"enable": False, "enable_text": False},
                "open_tracking": {"enable": False},
                "subscription_tracking": {"enable": False},
            },
            "mail_settings": {"sandbox_mode": {"enable": self._sandbox}},
        }

    def send(self, message: EmailMessage) -> None:
        response = self._client.post(
            self.API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=self.payload(message),
        )
        response.raise_for_status()


class InlineQueue:
    """Development: send immediately (still after commit, from a background task)."""

    def __init__(self, sender: EmailSender) -> None:
        self._sender = sender

    def enqueue(self, message: EmailMessage) -> None:
        self._sender.send(message)


class InMemoryQueue:
    """Tests: collect messages."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def enqueue(self, message: EmailMessage) -> None:
        self.messages.append(message)


class CloudTasksQueue:
    """Production: one Cloud Task per email, delivered to the worker with an OIDC token."""

    def __init__(self, queue_name: str, worker_url: str, service_account: str) -> None:
        from google.cloud import tasks_v2

        self._client = tasks_v2.CloudTasksClient()
        self._queue = queue_name
        self._url = worker_url.rstrip("/") + "/internal/tasks/email"
        self._service_account = service_account
        self._audience = worker_url.rstrip("/")

    def enqueue(self, message: EmailMessage) -> None:
        self._client.create_task(
            request={
                "parent": self._queue,
                "task": {
                    "http_request": {
                        "http_method": "POST",
                        "url": self._url,
                        "headers": {"Content-Type": "application/json"},
                        "body": message.to_json(),
                        "oidc_token": {
                            "service_account_email": self._service_account,
                            "audience": self._audience,
                        },
                    }
                },
            }
        )


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_sender == "sendgrid":
        # Only the mailer sends, so only it needs the key.
        if settings.sendgrid_api_key is None:
            raise RuntimeError("PIPE1_SENDGRID_API_KEY is required to send email")
        return SendGridSender(
            settings.sendgrid_api_key.get_secret_value(),
            settings.email_from,
            sandbox=settings.sendgrid_sandbox,
        )
    return ConsoleSender()


def build_queue(settings: Settings) -> EmailQueue:
    if settings.email_queue == "cloud_tasks":
        assert settings.cloud_tasks_queue and settings.worker_url and settings.tasks_service_account
        return CloudTasksQueue(
            settings.cloud_tasks_queue,
            settings.mailer_url or settings.worker_url,
            settings.tasks_service_account,
        )
    return InlineQueue(build_sender(settings))
