"""Email queueing and sending (docs/Web_Auth_Spec.md §11).

The API never talks to Resend during a request. It enqueues a message after the request's
transaction has committed (``db.after_commit``), so provider latency or outages cannot change
login or reset response times (which would reveal whether an account exists). In production the
queue is Cloud Tasks and the mailer sends; a single-host deployment uses BackgroundQueue.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx2

from pipe1_server.email.templates import TEMPLATES, mask_email, render
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


class PermanentEmailError(Exception):
    """The provider rejected the message itself (e.g. an invalid address); retrying cannot help."""


# 401/403 (bad key, unverified sending domain) are configuration errors: keep retrying so messages
# go out once fixed. 408/429 (rate limit, daily quota) are transient by definition.
_RETRYABLE_CLIENT_ERRORS = {401, 403, 408, 429}


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class EmailQueue(Protocol):
    def enqueue(self, message: EmailMessage) -> None: ...


class ConsoleSender:
    """Development only: logs the rendered email, including links."""

    def send(self, message: EmailMessage) -> None:
        rendered = render(message.template, message.data)
        logger.warning("EMAIL to=%s subject=%s\n%s", message.to, rendered.subject, rendered.text)


class ResendSender:
    """Click and open tracking are per-domain settings in Resend (off by default); keep them off
    so token links are never rewritten through a tracking domain."""

    API_URL = "https://api.resend.com/emails"

    def __init__(
        self, api_key: str, from_address: str, *, client: httpx2.Client | None = None
    ) -> None:
        self._api_key = api_key
        self._from = from_address
        self._client = client or httpx2.Client(timeout=10.0)

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        rendered = render(message.template, message.data)
        return {
            "from": self._from,
            "to": [message.to],
            "subject": rendered.subject,
            "text": rendered.text,
            "html": rendered.html,
        }

    def send(self, message: EmailMessage) -> None:
        response = self._client.post(
            self.API_URL,
            # Resend rejects requests without a User-Agent.
            headers={"Authorization": f"Bearer {self._api_key}", "User-Agent": "pipe1-server"},
            json=self.payload(message),
        )
        status = response.status_code
        if 400 <= status < 500 and status not in _RETRYABLE_CLIENT_ERRORS:
            raise PermanentEmailError(f"Resend rejected it ({status}): {_error(response)}")
        response.raise_for_status()


def _error(response: httpx2.Response) -> str:
    """Resend's error message names the offending field; it never echoes the body."""
    try:
        body = response.json()
        return f"{body.get('name')}: {body.get('message')}"
    except (ValueError, AttributeError):
        return response.reason_phrase


class InlineQueue:
    """Tests and one-off commands: send immediately, in the caller's thread."""

    def __init__(self, sender: EmailSender) -> None:
        self._sender = sender

    def enqueue(self, message: EmailMessage) -> None:
        self._sender.send(message)


class BackgroundQueue:
    """Single-host deployments (the dev VM): a daemon thread sends, retrying transient failures,
    so requests never wait for the provider. Messages still queued when the process stops are
    lost; production uses Cloud Tasks, which persists and retries them."""

    RETRY_DELAYS_SECONDS = (5.0, 30.0, 120.0, 600.0)

    def __init__(
        self,
        sender: EmailSender,
        *,
        retry_delays: Sequence[float] = RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        max_queued: int = 1000,
    ) -> None:
        self._sender = sender
        self._retry_delays = tuple(retry_delays)
        self._sleep = sleep
        self._queue: queue.Queue[EmailMessage] = queue.Queue(maxsize=max_queued)
        threading.Thread(target=self._run, name="pipe1-email", daemon=True).start()

    def enqueue(self, message: EmailMessage) -> None:
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            logger.error("email queue full; dropped message to %s", mask_email(message.to))

    def join(self) -> None:
        """Wait until every queued message has been sent or given up on (tests)."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            try:
                self._deliver(message)
            finally:
                self._queue.task_done()

    def _deliver(self, message: EmailMessage) -> None:
        recipient = mask_email(message.to)
        for attempt, delay in enumerate((*self._retry_delays, None), start=1):
            try:
                self._sender.send(message)
                return
            except PermanentEmailError as error:
                logger.error("email %s to %s not sent: %s", message.template, recipient, error)
                return
            except Exception:  # noqa: BLE001 - provider and network errors are retried
                if delay is None:
                    logger.exception(
                        "email %s to %s failed %d times; giving up",
                        message.template,
                        recipient,
                        attempt,
                    )
                    return
                logger.warning(
                    "email %s to %s failed (attempt %d); retrying in %.0fs",
                    message.template,
                    recipient,
                    attempt,
                    delay,
                    exc_info=True,
                )
                self._sleep(delay)


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
    if settings.email_sender == "resend":
        # Only the mailer sends, so only it needs the key.
        if settings.resend_api_key is None:
            raise RuntimeError("PIPE1_RESEND_API_KEY is required to send email")
        return ResendSender(settings.resend_api_key.get_secret_value(), settings.email_from)
    return ConsoleSender()


def build_queue(settings: Settings) -> EmailQueue:
    if settings.email_queue == "cloud_tasks":
        assert settings.cloud_tasks_queue and settings.worker_url and settings.tasks_service_account
        return CloudTasksQueue(
            settings.cloud_tasks_queue,
            settings.mailer_url or settings.worker_url,
            settings.tasks_service_account,
        )
    if settings.email_queue == "background":
        return BackgroundQueue(build_sender(settings))
    return InlineQueue(build_sender(settings))
