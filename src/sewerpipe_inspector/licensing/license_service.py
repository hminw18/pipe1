from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sewerpipe_inspector.licensing.api_client import LicenseApiClientProtocol
from sewerpipe_inspector.licensing.device_identity import DeviceIdentity
from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier
from sewerpipe_inspector.licensing.errors import LicenseApiError, LicenseConnectionError
from sewerpipe_inspector.licensing.local_store import LocalLicenseStore


@dataclass(frozen=True)
class LicenseStatus:
    status: str
    activation_id: str | None = None
    reason: str | None = None
    masked_license_key: str | None = None
    license_id: str | None = None
    device_upload_token: str | None = None
    features: dict[str, bool] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def can_use_feature(self, feature_key: str) -> bool:
        return bool(self.features.get(feature_key))


def mask_license_key(raw_key: str) -> str:
    return raw_key[:10]


def normalize_license_key(raw_key: str) -> str:
    return raw_key.strip().replace(" ", "").upper()


class LicenseService:
    def __init__(
        self,
        *,
        store: LocalLicenseStore,
        device_identity: DeviceIdentity,
        client: LicenseApiClientProtocol,
        verifier: EntitlementVerifier,
        app_version: str,
    ) -> None:
        self.store = store
        self.device_identity = device_identity
        self.client = client
        self.verifier = verifier
        self.app_version = app_version

    def activate(self, license_key: str) -> LicenseStatus:
        device_id = self.device_identity.get_or_create()
        response = self.client.activate(
            license_key=license_key,
            device_id=device_id,
            device_name=self.device_identity.device_name(),
            os_name=self.device_identity.os_name(),
            os_version=self.device_identity.os_version(),
            app_version=self.app_version,
        )
        entitlement = response["entitlement"]
        payload = self.verifier.verify(entitlement, expected_device_id=device_id)
        self.store.save_activation_state(
            activation_id=response["activation_id"],
            masked_license_key=mask_license_key(license_key),
            entitlement=entitlement,
            device_upload_token=response.get("device_upload_token"),
        )
        return self._status_from_payload(
            "active",
            response["activation_id"],
            mask_license_key(license_key),
            payload,
            response.get("device_upload_token"),
        )

    def current_status(self, *, validate_online: bool = False) -> LicenseStatus:
        state = self.store.load()
        if state is None:
            return LicenseStatus(status="inactive", reason="license is not activated")
        device_id = self.device_identity.get_or_create()
        entitlement = state.get("entitlement")
        if not isinstance(entitlement, dict):
            return LicenseStatus(status="invalid", reason="missing entitlement")

        activation_id = str(state.get("activation_id") or "")
        transient_validation_error: str | None = None
        if validate_online:
            try:
                self.verifier.verify(
                    entitlement,
                    expected_device_id=device_id,
                    enforce_time_limits=False,
                    enforce_license_status=False,
                )
            except ValueError as exc:
                return LicenseStatus(status="invalid", reason=str(exc))
            if not activation_id:
                return LicenseStatus(status="invalid", reason="missing activation id")
            try:
                response = self.client.validate(
                    activation_id=activation_id,
                    device_id=device_id,
                    app_version=self.app_version,
                )
            except LicenseConnectionError as exc:
                transient_validation_error = str(exc)
            except LicenseApiError as exc:
                if not _is_transient_validation_error(exc):
                    return LicenseStatus(status="invalid", reason=exc.message)
                transient_validation_error = exc.message
            else:
                refreshed = response.get("entitlement")
                if not isinstance(refreshed, dict):
                    transient_validation_error = (
                        "라이선스 서버가 새 인증 정보를 반환하지 않았습니다."
                    )
                else:
                    try:
                        payload = self.verifier.verify(
                            refreshed, expected_device_id=device_id
                        )
                    except ValueError as exc:
                        return LicenseStatus(status="invalid", reason=str(exc))
                    upload_token = response.get(
                        "device_upload_token", state.get("device_upload_token")
                    )
                    self.store.save_activation_state(
                        activation_id=activation_id,
                        masked_license_key=str(state.get("masked_license_key") or ""),
                        entitlement=refreshed,
                        device_upload_token=upload_token,
                    )
                    return self._status_from_payload(
                        "active",
                        activation_id,
                        state.get("masked_license_key"),
                        payload,
                        upload_token,
                    )

        try:
            payload = self.verifier.verify(entitlement, expected_device_id=device_id)
        except ValueError as exc:
            return LicenseStatus(status="invalid", reason=str(exc))

        return self._status_from_payload(
            "active",
            activation_id or None,
            state.get("masked_license_key"),
            payload,
            state.get("device_upload_token"),
            reason=transient_validation_error,
        )

    @staticmethod
    def _status_from_payload(
        status: str,
        activation_id: str | None,
        masked_license_key: str | None,
        payload: dict[str, Any],
        device_upload_token: str | None,
        *,
        reason: str | None = None,
    ) -> LicenseStatus:
        features = {
            str(key): bool(value)
            for key, value in (payload.get("features") or {}).items()
        }
        return LicenseStatus(
            status=status,
            activation_id=activation_id,
            reason=reason,
            masked_license_key=masked_license_key,
            license_id=payload.get("license_id"),
            device_upload_token=device_upload_token,
            features=features,
            payload=payload,
        )


def _is_transient_validation_error(exc: LicenseApiError) -> bool:
    return exc.status_code in {408, 429} or exc.status_code >= 500
