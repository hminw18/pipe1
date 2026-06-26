from __future__ import annotations

import hashlib
import secrets
import string
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from pipe1_license_server.db import init_db, session_scope
from pipe1_license_server.models import (
    AdminAuditEvent,
    DeviceActivation,
    License,
    LicenseFeature,
    LicenseKey,
    LicenseUsageQuota,
    LicenseUsageEvent,
    Organization,
)
from pipe1_license_server.settings import ServerSettings


def hash_license_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _new_id(prefix: str) -> str:
    id_prefix = f"{prefix}_"
    if len(id_prefix) >= 36:
        raise ValueError("id prefix is too long")
    return f"{id_prefix}{uuid4().hex[: 36 - len(id_prefix)]}"


def _generate_raw_license_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "PIPE1-" + "-".join(groups)


class AdminService:
    def __init__(self, settings: ServerSettings) -> None:
        self.settings = settings
        init_db(settings)

    def create_organization(
        self, name: str, contact_email: str | None, *, actor: str = "developer"
    ) -> str:
        organization = Organization(
            id=_new_id("org"),
            name=name,
            contact_email=contact_email,
            status="active",
        )
        with session_scope(self.settings) as session:
            session.add(organization)
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="organization.create",
                    target_type="organization",
                    target_id=organization.id,
                    metadata_json={"name": name, "contact_email": contact_email},
                )
            )
        return organization.id

    def create_license(
        self,
        *,
        organization_id: str,
        plan: str,
        device_limit: int,
        expires_at: str | datetime,
        features: dict[str, bool] | None = None,
        ai_quota: dict[str, Any] | None = None,
        starts_at: str | datetime | None = None,
        actor: str = "developer",
    ) -> str:
        license_id = _new_id("lic")
        started_at = _parse_datetime(starts_at) or _now()
        expires = _parse_datetime(expires_at)
        if expires is None:
            raise ValueError("expires_at is required")

        with session_scope(self.settings) as session:
            organization = session.get(Organization, organization_id)
            if organization is None:
                raise ValueError("organization not found")
            license_row = License(
                id=license_id,
                organization_id=organization_id,
                plan=plan,
                status="active",
                seat_model="device",
                device_limit=device_limit,
                starts_at=started_at,
                expires_at=expires,
                offline_grace_days=self.settings.default_offline_grace_days,
            )
            session.add(license_row)
            for feature_key, enabled in (features or {}).items():
                session.add(
                    LicenseFeature(
                        id=_new_id("feature"),
                        license_id=license_id,
                        feature_key=feature_key,
                        enabled=bool(enabled),
                        metadata_json={},
                    )
                )
            if ai_quota is not None:
                feature_key = str(ai_quota.get("feature_key", "ai_assist"))
                session.add(
                    LicenseUsageQuota(
                        id=_new_id("quota"),
                        license_id=license_id,
                        feature_key=feature_key,
                        period=str(ai_quota.get("period", "monthly")),
                        unit=str(ai_quota.get("unit", "credit")),
                        limit=int(ai_quota.get("limit", 0)),
                        used=int(ai_quota.get("used", 0)),
                        reset_at=_parse_datetime(ai_quota.get("reset_at")),
                        overage_policy=str(ai_quota.get("overage_policy", "block")),
                    )
                )
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license.create",
                    target_type="license",
                    target_id=license_id,
                    metadata_json={
                        "organization_id": organization_id,
                        "plan": plan,
                        "device_limit": device_limit,
                    },
                )
            )
        return license_id

    def generate_license_key(
        self, license_id: str, *, key_type: str = "production", actor: str = "developer"
    ) -> str:
        raw_key = _generate_raw_license_key()
        key = LicenseKey(
            id=_new_id("key"),
            license_id=license_id,
            key_prefix=raw_key[:10],
            key_hash=hash_license_key(raw_key),
            status="active",
            key_type=key_type,
            issued_at=_now(),
        )
        with session_scope(self.settings) as session:
            if session.get(License, license_id) is None:
                raise ValueError("license not found")
            session.add(key)
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license_key.issue",
                    target_type="license_key",
                    target_id=key.id,
                    metadata_json={"license_id": license_id, "key_prefix": key.key_prefix},
                )
            )
        return raw_key

    def find_license_key_by_prefix(self, key_prefix: str) -> LicenseKey | None:
        with session_scope(self.settings) as session:
            result = session.execute(
                select(LicenseKey).where(LicenseKey.key_prefix == key_prefix)
            ).scalar_one_or_none()
            if result is not None:
                session.expunge(result)
            return result

    def revoke_license_key(self, key_prefix: str, *, actor: str = "developer") -> None:
        with session_scope(self.settings) as session:
            key = session.execute(
                select(LicenseKey).where(LicenseKey.key_prefix == key_prefix)
            ).scalar_one_or_none()
            if key is None:
                raise ValueError("license key not found")
            key.status = "revoked"
            key.revoked_at = _now()
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license_key.revoke",
                    target_type="license_key",
                    target_id=key.id,
                    metadata_json={"key_prefix": key_prefix},
                )
            )

    def rotate_license_key(
        self,
        key_prefix: str,
        *,
        key_type: str = "production",
        actor: str = "developer",
    ) -> str:
        raw_key = _generate_raw_license_key()
        with session_scope(self.settings) as session:
            old_key = session.execute(
                select(LicenseKey).where(LicenseKey.key_prefix == key_prefix)
            ).scalar_one_or_none()
            if old_key is None:
                raise ValueError("license key not found")
            new_key = LicenseKey(
                id=_new_id("key"),
                license_id=old_key.license_id,
                key_prefix=raw_key[:10],
                key_hash=hash_license_key(raw_key),
                status="active",
                key_type=key_type,
                issued_at=_now(),
            )
            old_key.status = "revoked"
            old_key.revoked_at = _now()
            old_key.replaced_by_key_id = new_key.id
            session.add(new_key)
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license_key.rotate",
                    target_type="license_key",
                    target_id=old_key.id,
                    metadata_json={
                        "old_key_prefix": key_prefix,
                        "new_key_prefix": new_key.key_prefix,
                    },
                )
            )
        return raw_key

    def deactivate_device(
        self, activation_id: str, *, actor: str = "developer"
    ) -> None:
        with session_scope(self.settings) as session:
            activation = session.get(DeviceActivation, activation_id)
            if activation is None:
                raise ValueError("activation not found")
            activation.status = "deactivated"
            activation.deactivated_at = _now()
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="device.deactivate",
                    target_type="device_activation",
                    target_id=activation_id,
                    metadata_json={
                        "license_id": activation.license_id,
                        "device_id": activation.device_id,
                    },
                )
            )

    def list_device_activations(
        self, license_id: str, *, active_only: bool = True
    ) -> list[dict[str, Any]]:
        with session_scope(self.settings) as session:
            query = select(DeviceActivation).where(DeviceActivation.license_id == license_id)
            if active_only:
                query = query.where(DeviceActivation.status == "active")
            rows = session.execute(query.order_by(DeviceActivation.activated_at.asc())).scalars()
            return [
                {
                    "id": row.id,
                    "license_id": row.license_id,
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "os_name": row.os_name,
                    "os_version": row.os_version,
                    "app_version": row.app_version,
                    "status": row.status,
                    "activated_at": row.activated_at.isoformat(),
                    "last_validated_at": row.last_validated_at.isoformat()
                    if row.last_validated_at
                    else None,
                }
                for row in rows
            ]

    def set_feature(
        self,
        license_id: str,
        feature_key: str,
        enabled: bool,
        *,
        actor: str = "developer",
    ) -> None:
        with session_scope(self.settings) as session:
            if session.get(License, license_id) is None:
                raise ValueError("license not found")
            feature = session.execute(
                select(LicenseFeature).where(
                    LicenseFeature.license_id == license_id,
                    LicenseFeature.feature_key == feature_key,
                )
            ).scalar_one_or_none()
            if feature is None:
                feature = LicenseFeature(
                    id=_new_id("feature"),
                    license_id=license_id,
                    feature_key=feature_key,
                    enabled=enabled,
                    metadata_json={},
                )
                session.add(feature)
            else:
                feature.enabled = enabled
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license_feature.set",
                    target_type="license",
                    target_id=license_id,
                    metadata_json={"feature_key": feature_key, "enabled": enabled},
                )
            )

    def set_ai_quota(
        self,
        license_id: str,
        *,
        feature_key: str,
        limit: int,
        unit: str,
        period: str,
        used: int = 0,
        overage_policy: str = "block",
        reset_at: str | datetime | None = None,
        actor: str = "developer",
    ) -> None:
        with session_scope(self.settings) as session:
            if session.get(License, license_id) is None:
                raise ValueError("license not found")
            quota = session.execute(
                select(LicenseUsageQuota).where(
                    LicenseUsageQuota.license_id == license_id,
                    LicenseUsageQuota.feature_key == feature_key,
                )
            ).scalar_one_or_none()
            if quota is None:
                quota = LicenseUsageQuota(
                    id=_new_id("quota"),
                    license_id=license_id,
                    feature_key=feature_key,
                    period=period,
                    unit=unit,
                    limit=limit,
                    used=used,
                    reset_at=_parse_datetime(reset_at),
                    overage_policy=overage_policy,
                )
                session.add(quota)
            else:
                quota.period = period
                quota.unit = unit
                quota.limit = limit
                quota.used = used
                quota.reset_at = _parse_datetime(reset_at)
                quota.overage_policy = overage_policy
            session.add(
                AdminAuditEvent(
                    id=_new_id("audit"),
                    actor=actor,
                    action="license_quota.set",
                    target_type="license",
                    target_id=license_id,
                    metadata_json={
                        "feature_key": feature_key,
                        "limit": limit,
                        "unit": unit,
                        "period": period,
                    },
                )
            )

    def record_usage_event(
        self,
        license_id: str,
        *,
        feature_key: str,
        quantity: int,
        unit: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = _new_id("usage")
        with session_scope(self.settings) as session:
            session.add(
                LicenseUsageEvent(
                    id=event_id,
                    license_id=license_id,
                    feature_key=feature_key,
                    quantity=quantity,
                    unit=unit,
                    request_id=request_id,
                    metadata_json=metadata or {},
                )
            )
        return event_id

    def list_usage_events(self, license_id: str) -> list[dict[str, Any]]:
        with session_scope(self.settings) as session:
            events = session.execute(
                select(LicenseUsageEvent)
                .where(LicenseUsageEvent.license_id == license_id)
                .order_by(LicenseUsageEvent.created_at.asc())
            ).scalars()
            return [
                {
                    "id": event.id,
                    "feature_key": event.feature_key,
                    "quantity": event.quantity,
                    "unit": event.unit,
                    "request_id": event.request_id,
                    "metadata": event.metadata_json or {},
                }
                for event in events
            ]

    def list_audit_events(self) -> list[dict[str, Any]]:
        with session_scope(self.settings) as session:
            events = session.execute(
                select(AdminAuditEvent).order_by(AdminAuditEvent.created_at.asc())
            ).scalars()
            return [
                {
                    "id": event.id,
                    "actor": event.actor,
                    "action": event.action,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "metadata": event.metadata_json or {},
                }
                for event in events
            ]
