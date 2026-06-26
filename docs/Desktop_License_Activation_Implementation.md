# Pipe1 Desktop License Activation Implementation

## 1. Purpose

This document defines the standard implementation scope for license activation in the Pipe1 desktop app.

This is the desktop-side counterpart to `docs/License_Server_MVP_Implementation.md`.

The goal is to let field users activate Pipe1 with a license key once, then keep using the app locally without routine login prompts.

## 2. Product Rule

Pipe1 remains a local-first Windows desktop app.

License activation should:

- verify that the installation is allowed to run
- bind a license to a device activation
- cache a signed entitlement locally
- allow offline use until `offline_grace_until`
- avoid asking users to log in during normal use
- avoid sending the raw license key on every launch

License activation should not:

- move local inspection data to the server
- require a user account for the MVP
- block local report work just because a background validation request failed
- store the raw license key after activation
- store server secrets in the desktop app

## 3. Desktop Architecture

Recommended modules:

```text
src/sewerpipe_inspector/
  licensing/
    __init__.py
    api_client.py
    device_identity.py
    entitlement.py
    license_service.py
    local_store.py
    errors.py
  ui/
    license_dialog.py
```

Responsibilities:

- `api_client.py`: HTTP calls to license server.
- `device_identity.py`: stable device id generation and lookup.
- `entitlement.py`: signed entitlement parsing and signature verification.
- `license_service.py`: activation, validation, status decisions.
- `local_store.py`: local non-secret license state storage.
- `errors.py`: typed activation and validation errors.
- `license_dialog.py`: PySide activation/status UI.

The main app should depend on `LicenseService`, not on raw HTTP functions.

## 4. Runtime Flow

### 4.1 First Launch

1. App starts.
2. App loads local license state.
3. If no valid entitlement exists, app shows license activation prompt.
4. User enters license key.
5. App sends activation request to server.
6. Server returns signed entitlement.
7. App verifies signature.
8. App stores entitlement and activation metadata.
9. App opens normal local workspace flow.

### 4.2 Normal Launch

1. App starts.
2. App loads cached entitlement.
3. App verifies entitlement signature locally.
4. If entitlement is valid and within offline grace, app starts without blocking on network.
5. If background validation is due, app validates asynchronously after the main window is available.
6. If validation succeeds, app stores fresh entitlement.
7. If validation fails due to network, app keeps the last valid entitlement until grace expiry.

### 4.3 Grace Expired

1. App starts.
2. Entitlement signature is valid but `offline_grace_until` is in the past.
3. App requires online validation before production use.
4. If validation succeeds, app continues.
5. If validation fails, app shows license status page and keeps local data accessible where possible.

## 5. Local Storage

### 5.1 Storage Locations

Do not store license state inside the selected inspection workspace.

Recommended Windows location:

```text
%APPDATA%\Pipe1\license_state.json
%APPDATA%\Pipe1\device_id
```

Recommended development fallback:

```text
~/.pipe1/license_state.json
~/.pipe1/device_id
```

Current app note:

- Existing settings use `~/.sewerpipe_inspector_settings.json`.
- Production license state should be separate from this legacy settings file.
- A later settings migration may move workspace preferences into `%APPDATA%\Pipe1`.

### 5.2 Secret vs Non-Secret State

Non-secret local state:

- activation id
- device id
- masked license key prefix
- signed entitlement envelope
- last validation timestamp
- last validation result
- license server base URL

Secret state:

- device credential, if the server issues one
- refresh token, only if account login is later added

MVP rule:

- The raw license key is not stored after activation.
- Signed entitlement is not secret, but must be integrity-checked.
- If a device credential exists, store it in Windows Credential Manager.

## 6. Device Identity

The desktop app should generate a stable random device id on first run.

Rules:

- Device id must be random, not derived directly from hardware identifiers.
- Device id must persist across app restarts.
- Device id may reset on reinstall unless future support policy requires otherwise.
- Device id is not secret.
- Device id should be included in signed entitlement.

Suggested format:

```text
pipe1-dev_01J...  # prefix + UUIDv7/ULID/random token
```

Avoid:

- using MAC address as the primary device id
- using Windows username as the device id
- using machine name as the only device id
- collecting hardware fingerprints unless legally reviewed

## 7. API Contract

### 7.1 Activation

Endpoint:

```text
POST /licenses/activate
```

Request:

```json
{
  "license_key": "PIPE1-XXXX-XXXX-XXXX-XXXX",
  "device_id": "pipe1-dev_...",
  "device_name": "optional machine name",
  "os_name": "Windows",
  "os_version": "11",
  "app_version": "0.1.0"
}
```

Rules:

- Send raw license key only to this endpoint or a future reactivation endpoint.
- Use HTTPS only.
- Use request timeout.
- Do not log raw license key.
- Do not retry invalid-key errors automatically.

### 7.2 Validation

Endpoint:

```text
POST /licenses/validate
```

Request:

```json
{
  "activation_id": "server-activation-id",
  "device_id": "pipe1-dev_...",
  "app_version": "0.1.0"
}
```

Rules:

- Do not include raw license key.
- Run in background when possible.
- Use backoff for network failures.
- Store fresh entitlement only after signature verification succeeds.

## 8. Entitlement Verification

The desktop app must verify the server-signed entitlement before trusting it.

MVP recommended algorithm:

- Ed25519 / EdDSA
- public key embedded in app resources or source
- private key never distributed with app

Envelope:

```json
{
  "payload": {
    "license_id": "...",
    "license_key_id": "...",
    "organization_id": "...",
    "license_status": "active",
    "plan": "standard",
    "features": {
      "local_report": true,
      "excel_export": true,
      "pdf_export": true,
      "training_upload": false,
      "ai_assist": false
    },
    "ai_quota": {
      "enabled": false,
      "period": "monthly",
      "limit": 0,
      "used": 0,
      "remaining": 0,
      "reset_at": null,
      "overage_policy": "block"
    },
    "expires_at": "2027-06-30T23:59:59Z",
    "offline_grace_until": "2026-07-09T00:00:00Z",
    "device_id": "pipe1-dev_...",
    "issued_at": "2026-06-25T00:00:00Z"
  },
  "signature": "base64url-signature",
  "alg": "EdDSA",
  "kid": "license-signing-key-2026-01"
}
```

Verification rules:

- Reject missing signature.
- Reject unsupported `alg`.
- Reject unknown `kid` unless a trusted key list includes it.
- Reject malformed timestamps.
- Reject payload where `device_id` does not match local device id.
- Reject expired entitlement for production features.
- Allow unknown future fields.
- Treat `features` as deny-by-default when a feature key is missing.

Clock rules:

- Allow small clock skew for `issued_at`.
- Do not trust local clock alone for extending grace.
- Store last successful server validation time.
- If local time appears to move backwards suspiciously, require online validation.

## 9. License Status Decisions

Suggested desktop statuses:

```text
not_activated
  - No entitlement exists.

active
  - Entitlement valid.
  - License status active/trial.
  - Current time before expires_at/offline_grace_until.

validation_due
  - Entitlement usable.
  - Background server validation should run.

offline_grace
  - Server unreachable.
  - Entitlement still inside offline_grace_until.

validation_required
  - Grace expired or local clock suspicious.
  - Online validation required.

expired
  - License expired.

revoked
  - Server reports license/key/device revoked.

device_limit_exceeded
  - Activation rejected due to device limit.

invalid
  - Entitlement invalid, malformed, or signature failed.
```

Feature gating:

- Local report work requires `local_report`.
- Excel export requires `excel_export`.
- PDF export requires `pdf_export`.
- Training upload requires `training_upload`.
- Future AI features require `ai_assist` and server-side quota approval.

MVP fallback:

- If entitlement is invalid or missing, block production use but keep local data files untouched.
- Do not delete local workspace data because of license state.

## 10. Desktop UI Requirements

### 10.1 Activation Dialog

Fields:

- license key input
- activation status message
- activate button
- cancel/exit button

Behavior:

- Trim whitespace.
- Normalize casing and hyphen formatting.
- Disable activate button while request is in progress.
- Show user-safe Korean messages.
- Do not show raw server traces.
- Do not persist raw key after success.

### 10.2 Status Surface

MVP UI:

- Show simple license status in settings or app shell.
- Show plan and expiry date.
- Show validation-required state clearly.
- Do not show hidden AI quota while no AI feature exists.
- Do not expose license key revocation or rotation controls.

### 10.3 Error Messages

Map server codes to user-safe messages:

- `INVALID_LICENSE_KEY`: 라이선스 키를 확인할 수 없습니다.
- `REVOKED_LICENSE_KEY`: 폐기된 라이선스 키입니다.
- `EXPIRED_LICENSE`: 라이선스가 만료되었습니다.
- `SUSPENDED_LICENSE`: 라이선스가 일시 중지되었습니다.
- `DEVICE_LIMIT_EXCEEDED`: 활성화 가능한 장치 수를 초과했습니다.
- `DEVICE_REVOKED`: 이 장치는 비활성화되었습니다.
- `UNSUPPORTED_APP_VERSION`: 이 버전은 더 이상 지원되지 않습니다.
- `RATE_LIMITED`: 잠시 후 다시 시도하세요.
- `SERVER_ERROR`: 서버 오류가 발생했습니다.

Every server error should also show a support request id when available.

## 11. Networking Requirements

Use a central API client.

Requirements:

- HTTPS only in production.
- Configurable base URL.
- Connect timeout.
- Read timeout.
- JSON schema validation.
- Typed errors.
- No raw license keys in logs.
- No entitlement private keys in the app.
- Background validation must not block video/report work while grace is valid.

Recommended dependency:

- `httpx` for sync HTTP client, or `requests` if keeping dependencies simpler.

PySide rule:

- Do not run network calls on the UI thread.
- Use `QThread`, worker object, or background executor.

## 12. App Startup Integration

Current startup flow:

```text
QApplication
  -> workspace selection
  -> Database
  -> StorageService
  -> MainWindow
```

Recommended production startup flow:

```text
QApplication
  -> LicenseService load local state
  -> if activation required: show activation dialog
  -> workspace selection
  -> Database
  -> StorageService
  -> MainWindow
  -> background validation when due
```

Rules:

- Do not ask for workspace selection before required activation unless product policy allows trial/offline demo mode.
- Do not store license state in selected workspace.
- Do not require server connection if a valid cached entitlement is inside grace.

## 13. Configuration

Required config values:

- `PIPE1_LICENSE_API_BASE_URL`
- `PIPE1_LICENSE_PUBLIC_KEYS`
- `PIPE1_APP_ENV`: `dev`, `staging`, `prod`
- `PIPE1_APP_VERSION`

Rules:

- Production build should use production API URL.
- Development build may use local or staging API URL.
- Public keys can be embedded because they are not secrets.
- Private keys must never be in desktop config.

## 14. Logging Requirements

Do log:

- activation started
- activation succeeded
- activation failed with code
- validation started
- validation succeeded
- validation failed with code
- entitlement signature verification failed

Do not log:

- raw license key
- full entitlement payload if it contains customer-identifying data
- device credential
- HTTP Authorization header
- raw server secrets

## 15. Test Requirements

Unit tests:

- device id creation and persistence
- license key normalization
- local store read/write
- entitlement signature verification
- invalid signature rejection
- mismatched device id rejection
- expired entitlement decision
- offline grace decision
- feature gating
- unknown entitlement fields ignored

Integration tests with fake server:

- activation success
- invalid key
- device limit exceeded
- validation success
- validation network failure while grace valid
- validation failure after grace expired
- revoked device response

Manual Windows tests:

- activation on clean install
- app restart after activation
- offline launch inside grace
- offline launch after grace
- server unavailable
- invalid system clock
- Korean Windows username/path

## 16. MVP Implementation Phases

### Phase 1 - Local License Foundation

- Add licensing package.
- Add device id generation.
- Add local license state store.
- Add entitlement envelope model.
- Add feature-gating decision model.
- Add tests.

### Phase 2 - Server API Client

- Add HTTP client.
- Add activation and validation calls.
- Add typed errors.
- Add request timeouts.
- Add fake server tests.

### Phase 3 - Entitlement Signing

- Add Ed25519 verification.
- Add public key configuration.
- Add `kid` support.
- Add invalid signature tests.

### Phase 4 - UI Integration

- Add activation dialog.
- Add license status display.
- Integrate startup gating.
- Add background validation.

### Phase 5 - Production Hardening

- Add Windows Credential Manager support if device credential exists.
- Add logging redaction.
- Add staging/prod config separation.
- Add packaged-app smoke tests.

## 17. Open Decisions

- Exact local app data path name: `Pipe1` vs `SewerPipeInspector`.
- Whether activation is required before workspace selection.
- Whether a trial/demo mode exists without activation.
- Whether a device credential is needed in MVP.
- Whether to use `httpx` or `requests`.
- Which Ed25519 library to use.
- Exact background validation interval.
- Whether server validation should happen on every successful report generation.

