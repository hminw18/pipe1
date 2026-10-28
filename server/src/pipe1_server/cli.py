"""Operator commands.

    uv run python -m pipe1_server.cli invite-platform-admin someone@example.com

Bootstraps the first platform admin; later ones are invited from the platform admin API.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from pipe1_server.db import session_scope
from pipe1_server.email.delivery import build_queue
from pipe1_server.security.invitations import InvitationError, create_invitation
from pipe1_server.settings import get_settings


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
    build_queue(settings).enqueue(message)
    print(f"Invitation sent to {message.to}.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipe1_server.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    invite = commands.add_parser("invite-platform-admin")
    invite.add_argument("email")
    args = parser.parse_args()
    try:
        if args.command == "invite-platform-admin":
            invite_platform_admin(args.email)
    except InvitationError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
