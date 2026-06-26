from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from pipe1_license_server.admin import AdminService
from pipe1_license_server.settings import ServerSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipe1-admin")
    subcommands = parser.add_subparsers(dest="resource", required=True)

    org = subcommands.add_parser("org")
    org_sub = org.add_subparsers(dest="action", required=True)
    org_create = org_sub.add_parser("create")
    org_create.add_argument("--name", required=True)
    org_create.add_argument("--contact-email")

    license_cmd = subcommands.add_parser("license")
    license_sub = license_cmd.add_subparsers(dest="action", required=True)
    license_create = license_sub.add_parser("create")
    license_create.add_argument("--org", required=True)
    license_create.add_argument("--plan", required=True)
    license_create.add_argument("--device-limit", type=int, required=True)
    license_create.add_argument("--expires-at", required=True)

    key = subcommands.add_parser("key")
    key_sub = key.add_subparsers(dest="action", required=True)
    key_generate = key_sub.add_parser("generate")
    key_generate.add_argument("--license", required=True)
    key_generate.add_argument("--type", default="production")
    key_revoke = key_sub.add_parser("revoke")
    key_revoke.add_argument("--key-prefix", required=True)
    key_rotate = key_sub.add_parser("rotate")
    key_rotate.add_argument("--key-prefix", required=True)

    devices = subcommands.add_parser("devices")
    devices_sub = devices.add_subparsers(dest="action", required=True)
    devices_list = devices_sub.add_parser("list")
    devices_list.add_argument("--license", required=True)

    device = subcommands.add_parser("device")
    device_sub = device.add_subparsers(dest="action", required=True)
    device_deactivate = device_sub.add_parser("deactivate")
    device_deactivate.add_argument("--activation", required=True)

    feature = subcommands.add_parser("feature")
    feature_sub = feature.add_subparsers(dest="action", required=True)
    feature_set = feature_sub.add_parser("set")
    feature_set.add_argument("--license", required=True)
    feature_set.add_argument("--feature", required=True)
    feature_set.add_argument("--enabled", choices=["true", "false"], required=True)

    quota = subcommands.add_parser("quota")
    quota_sub = quota.add_subparsers(dest="action", required=True)
    quota_set = quota_sub.add_parser("set")
    quota_set.add_argument("--license", required=True)
    quota_set.add_argument("--feature", required=True)
    quota_set.add_argument("--unit", required=True)
    quota_set.add_argument("--limit", type=int, required=True)
    quota_set.add_argument("--period", required=True)
    return parser


def run_cli(
    argv: list[str],
    *,
    settings: ServerSettings | None = None,
    stdout: TextIO | None = None,
) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    service = AdminService(settings or ServerSettings())
    output: dict[str, Any]

    if args.resource == "org" and args.action == "create":
        output = {
            "id": service.create_organization(args.name, args.contact_email),
            "name": args.name,
        }
    elif args.resource == "license" and args.action == "create":
        output = {
            "id": service.create_license(
                organization_id=args.org,
                plan=args.plan,
                device_limit=args.device_limit,
                expires_at=args.expires_at,
            )
        }
    elif args.resource == "key" and args.action == "generate":
        output = {
            "license_key": service.generate_license_key(
                args.license, key_type=args.type
            )
        }
    elif args.resource == "key" and args.action == "revoke":
        service.revoke_license_key(args.key_prefix)
        output = {"key_prefix": args.key_prefix, "status": "revoked"}
    elif args.resource == "key" and args.action == "rotate":
        output = {"license_key": service.rotate_license_key(args.key_prefix)}
    elif args.resource == "devices" and args.action == "list":
        output = {"devices": service.list_device_activations(args.license)}
    elif args.resource == "device" and args.action == "deactivate":
        service.deactivate_device(args.activation)
        output = {"activation_id": args.activation, "status": "deactivated"}
    elif args.resource == "feature" and args.action == "set":
        enabled = args.enabled == "true"
        service.set_feature(args.license, args.feature, enabled)
        output = {
            "license_id": args.license,
            "feature": args.feature,
            "enabled": enabled,
        }
    elif args.resource == "quota" and args.action == "set":
        service.set_ai_quota(
            args.license,
            feature_key=args.feature,
            unit=args.unit,
            limit=args.limit,
            period=args.period,
        )
        output = {
            "license_id": args.license,
            "feature": args.feature,
            "limit": args.limit,
        }
    else:  # pragma: no cover - argparse prevents this path
        raise SystemExit(2)

    target = stdout or sys.stdout
    target.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
    return output


def main() -> None:
    run_cli(sys.argv[1:])
