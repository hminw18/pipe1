"""Operator commands.

    uv run python -m pipe1_server.cli invite-platform-admin someone@example.com
    uv run python -m pipe1_server.cli send-test-email someone@example.com

invite-platform-admin bootstraps the first platform admin; later ones are invited from the
platform admin API. send-test-email checks the email settings (e.g. a new Resend key).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from pipe1_server.db import session_scope
from pipe1_server.email.delivery import (
    EmailMessage,
    EmailQueue,
    InlineQueue,
    build_queue,
    build_sender,
)
from pipe1_server.security.invitations import InvitationError, create_invitation
from pipe1_server.settings import Settings, get_settings


def _outbox(settings: Settings) -> EmailQueue:
    # This process exits right after enqueueing, which would drop messages still waiting in a
    # background thread: send in the foreground instead.
    if settings.email_queue == "background":
        return InlineQueue(build_sender(settings))
    return build_queue(settings)


def invite_platform_admin(email: str) -> None:
    settings = get_settings()
    with session_scope() as db:
        _, message = create_invitation(
            db,
            email=email,
            role="platform_admin",
            organization_id=None,
            invited_by=None,
            now=datetime.now(UTC),
            settings=settings,
        )
    _outbox(settings).enqueue(message)
    print(f"Invitation sent to {message.to}.")


def send_test_email(email: str) -> None:
    settings = get_settings()
    _outbox(settings).enqueue(EmailMessage(to=email, template="delivery_test"))
    print(f"Test email sent to {email} with {settings.email_sender} ({settings.email_queue}).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipe1_server.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    invite = commands.add_parser("invite-platform-admin")
    invite.add_argument("email")
    test = commands.add_parser("send-test-email")
    test.add_argument("email")
    args = parser.parse_args()
    try:
        if args.command == "invite-platform-admin":
            invite_platform_admin(args.email)
        elif args.command == "send-test-email":
            send_test_email(args.email)
    except InvitationError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
