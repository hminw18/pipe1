# Pipe1 Distribution Readiness Requirements

## 1. Purpose

This document defines the production-readiness requirements for distributing Pipe1 as a Windows desktop application.

Current product baseline:

- Windows 10/11 desktop application
- Single-user local workspace
- SQLite local database
- Local video review, defect capture, Excel/PDF report export

Production distribution adds:

- User authentication
- License or entitlement validation
- Optional training-data upload to a server
- Legal consent and disclosure surfaces
- Windows packaging, signing, and installation flow

## 2. Design Principle

Pipe1 should remain local-first.

Authentication and server connectivity should unlock production features, licensing, update eligibility, and optional server upload. They should not make ordinary local inspection work fragile when the network is temporarily unavailable.

Recommended model:

```text
Desktop App
  - Local workspace
  - Local SQLite DB
  - Local captures and reports
  - Cached signed entitlement
  - Upload queue

Auth/API Server
  - User identity
  - Organization/account membership
  - License entitlement
  - Device activation records
  - Upload intake API
```

## 3. Authentication and Licensing Structure

### 3.1 Recommended Approach

Use entitlement validation as the core production gate.

For Pipe1's target users, a mandatory account login on every desktop installation may feel like unnecessary friction. The first production UX should therefore support license-key or activation-code based device activation, while keeping account-based login available for administrators, subscriptions, AI usage, and optional cloud features.

Recommended default:

```text
Field User
  - Opens local desktop app
  - Enters license key or activation code once
  - App validates license and activates device
  - App works locally during the offline grace period

Admin / Purchaser
  - Manages account, subscription, seats, invoices, and AI quota in a web portal
  - Issues activation keys or invites users
```

The desktop app should ask the server whether the current license, organization, user, or device has the right to run the product.

Authentication answers:

- Who is the user?
- Which account or organization do they belong to?

Entitlement answers:

- Is this user or organization allowed to use Pipe1?
- Is this license key or activation code valid?
- Which features are enabled?
- Until when is the license valid?
- Is this device activated?

### 3.2 Initial No-Portal Licensing Model

Implementation details live in the license server repository's `docs/License_Server_MVP_Implementation.md`.

The first commercial version may ship without an admin web portal.

This is acceptable if the backend license data model is still designed as if a portal will be added later.

Initial operating model:

```text
Developer / Operator
  - Creates customer organization record
  - Creates license record
  - Generates license key or activation code
  - Sends license key to customer manually
  - Handles renewal, revocation, and device reset manually

Customer
  - Receives license key
  - Enters key in Pipe1 desktop app
  - Uses app locally after activation
```

Required internal admin tools for MVP:

- CLI command or protected admin script to create an organization.
- CLI command or protected admin script to create a license.
- CLI command or protected admin script to generate/revoke license keys.
- CLI command or protected admin script to rotate or replace a license key.
- CLI command or protected admin script to list active devices for a license.
- CLI command or protected admin script to deactivate a device.
- CLI command or protected admin script to set plan features.
- CLI command or protected admin script to set AI quota values.
- CLI command or protected admin script to inspect AI usage history.
- Database access policy and audit log for manual operations.

Rules:

- Do not hard-code issued license keys in the app.
- Do not generate license validity only inside the desktop app.
- Keep license records on the server even if there is no portal.
- Keep device activation records on the server even if the developer manages them manually.
- Design the database so a future web portal can use the same tables and APIs.
- Keep customer-facing support actions simple: issue key, extend expiry, revoke key, reset device.

Minimum backend entities:

- `organizations`
- `licenses`
- `license_keys`
- `device_activations`
- `license_features`
- `license_usage_quotas`
- `license_usage_events`
- `entitlement_snapshots`
- `admin_audit_events`

Minimum activation API:

- `POST /licenses/activate`
- `POST /licenses/validate`
- `POST /licenses/deactivate-device`, admin only
- `POST /licenses/revoke-key`, admin only
- `POST /licenses/keys/rotate`, admin only
- `POST /licenses/quotas/set`, admin only

This direction keeps the first release operationally simple while avoiding a throwaway licensing implementation.

### 3.3 Low-Friction Activation Flow

Desktop implementation details live in `docs/Desktop_License_Activation_Implementation.md`.

Recommended first-run flow:

1. User clicks `라이선스 활성화`.
2. User enters a license key or activation code.
3. App sends the key, generated device id, app version, and OS information to the server.
4. Server validates the license and device allowance.
5. Server returns a signed entitlement payload.
6. App caches the signed entitlement locally.
7. App stores any device credential in Windows Credential Manager.
8. User can continue using the app locally without routine login prompts.

Rules:

- Do not require an end user account unless the purchased product plan requires named users.
- Do not ask for browser login during normal daily app launch if a valid cached entitlement exists.
- Treat license activation as a one-time or occasional event.
- Show license status quietly in the app shell or settings screen.
- Keep local inspection work available during offline grace.
- Require online validation only when activation, renewal, revocation check, paid AI usage, or server upload is needed.

This flow keeps the product feeling like a local Windows app while preserving future subscription and usage-metering options on the server.

### 3.4 Optional Account Login Flow

Recommended flow:

1. User clicks `로그인`.
2. App opens the system browser for login.
3. Server uses OAuth 2.0 / OpenID Connect Authorization Code with PKCE.
4. Browser redirects back to the desktop app through a loopback callback or custom URL scheme.
5. App receives an authorization code.
6. App exchanges the code for tokens.
7. App stores refresh credentials in Windows Credential Manager.
8. App stores only non-secret account state in local settings or SQLite.

Rules:

- Do not embed a client secret in the desktop app.
- Do not store access tokens, refresh tokens, passwords, or API keys in plain JSON or SQLite.
- Do not implement password verification locally.
- Use short-lived access tokens.
- Use refresh-token rotation where the auth provider supports it.
- Support logout by clearing local credentials and cached account state.

Fallback flow:

- Device-code login may be supported for locked-down Windows environments where browser callback handling is unreliable.

Use account login when:

- The customer uses named-user licensing.
- The customer needs SSO.
- AI usage quota must be tied to a specific user.
- Training-data upload consent must be recorded per user.
- The user needs cloud or web-portal features.

### 3.5 Enterprise Offline License File

Some customers may require computers that cannot contact the internet.

For those environments, support a signed offline license file as an enterprise-only fallback.

Recommended flow:

1. Admin provides machine or organization information to the vendor.
2. Vendor generates a signed license file.
3. User imports the license file in Pipe1.
4. App verifies the signature locally.
5. App enables local features until the license expiry date.

Rules:

- Offline license files should not enable paid server-cost features such as AI analysis.
- Offline licenses should have a clear expiry date.
- Offline license files should be signed, not encrypted-only.
- Offline license validation must not depend on secrets embedded in the app.

### 3.6 License and Device Activation

On first successful activation or login, the app should register the installation with the server.

Suggested device registration fields:

- generated `device_id`
- app version
- OS version
- machine name, optional
- hashed hardware signal, optional
- first activation time
- last validation time

The server should return a signed entitlement payload.

Suggested entitlement fields:

- `license_id`
- `license_key_id`, optional
- `account_id`
- `organization_id`
- `user_id`, optional
- `license_status`: `active`, `trial`, `expired`, `revoked`
- `plan`
- `features`
- `seat_model`: `device`, `named_user`, `floating`, `enterprise_offline`
- `ai_quota`, optional
- `expires_at`
- `offline_grace_until`
- `device_id`
- `issued_at`
- `signature`

Suggested `ai_quota` fields:

- `enabled`
- `period`: `monthly`, `annual`, `contract`
- `limit`
- `used`
- `remaining`
- `reset_at`
- `overage_policy`: `block`, `allow_and_invoice`, `manual_approval`

MVP rule:

- Include AI quota fields in server data and entitlement payloads even if the desktop UI does not expose AI controls yet.
- Do not show AI quota to the user until an AI feature is enabled.
- Do not allow offline license files to enable AI features that create server-side cost.

The desktop app may cache the signed entitlement and use it while offline.

Recommended offline policy:

- Allow normal use during a grace period after the last successful validation.
- Show a warning when validation is stale.
- Block production-only features after grace expiry.
- Keep local data accessible wherever legally and commercially acceptable.

Initial default:

- 14 days offline grace for paid accounts
- 7 days offline grace for trial accounts
- Immediate block for revoked licenses once the revocation is observed online

### 3.7 Local Auth State

The app should separate secret and non-secret state.

Secret state:

- refresh token
- device credential, if used

Storage:

- Windows Credential Manager
- macOS Keychain only for development machines
- never project workspace files

Non-secret state:

- last login email
- account display name
- organization display name
- license display name or masked license key
- cached signed entitlement
- last validation timestamp
- upload permission state

Storage:

- app settings file or local SQLite table

### 3.8 Hidden License Key and AI Quota Management

The MVP desktop app should not expose license-key management or AI quota management to normal end users.

However, the backend should include these capabilities from the beginning so that future web portal, subscription billing, and AI features do not require a licensing redesign.

Hidden/internal capabilities:

- Create, activate, revoke, rotate, and replace license keys.
- Track which key activated which device.
- Track key status: `active`, `revoked`, `expired`, `replaced`.
- Track key type: `production`, `trial`, `demo`, `internal`, `support`.
- Track plan features independently from the visible desktop UI.
- Store AI quota limits and usage even before AI UI is released.
- Record every admin operation in an audit log.
- Keep all management operations server-side.

Desktop MVP behavior:

- Show only simple license status to users.
- Do not show AI quota while no AI feature is visible.
- Do not show license key rotation or revocation controls.
- Receive hidden feature and quota fields in entitlement payloads.
- Ignore unknown entitlement fields safely for forward compatibility.

Server-side AI quota model:

- Quota belongs to a license or organization, not to a local workspace.
- Usage events should be recorded before or immediately after a server-cost AI operation.
- Server decides whether an AI request is allowed.
- Desktop app must not make billing or quota decisions locally.

Suggested AI usage event fields:

- `id`
- `license_id`
- `organization_id`, optional
- `device_id`
- `user_id`, optional
- `feature`
- `quantity`
- `unit`: `credit`, `image`, `request`, `token`
- `period_start`
- `period_end`
- `created_at`
- `request_id`
- `metadata_json`

## 4. Training Data Upload

Detailed requirements live in `docs/Training_Data_Upload_Requirements.md`.

### 4.1 Upload Principle

Training-data upload must be explicit, auditable, and resumable.

The product should not silently upload inspection videos, captures, reports, or defect metadata without user-facing consent.

### 4.2 Uploadable Data Types

Potential upload payloads:

- defect capture image
- defect metadata
- report metadata
- pipe/manhole metadata
- OCR/depth ROI metadata
- anonymized app diagnostics
- full video, only when separately allowed

Full video upload should be treated as a separate high-risk permission because CCTV inspection videos may contain location, infrastructure, customer, and public-works data.

### 4.3 Consent Model

The app should support separate consents:

- Required account/license processing
- Optional product diagnostics
- Optional defect-image training data
- Optional full-video training data

Each consent record should include:

- consent type
- accepted version
- accepted timestamp
- account id
- device id
- app version

Consent changes should be revocable from settings.

### 4.4 Upload Queue

Uploads should use a local queue so field work is not blocked by network quality.

Suggested queue fields:

- `id`
- `report_id`
- `defect_id`, optional
- `payload_type`
- `local_path`, optional
- `metadata_json`
- `status`: `pending`, `uploading`, `uploaded`, `failed`, `skipped`
- `retry_count`
- `last_error`
- `created_at`
- `updated_at`
- `uploaded_at`
- `server_object_id`

Rules:

- Use HTTPS only.
- Retry with backoff.
- Upload metadata before binary files.
- Use checksums for binary files.
- Support cancellation.
- Never delete local data because upload succeeded.
- Never block local report generation because upload failed.

## 5. Backend Stack Recommendation

Recommended MVP backend stack:

```text
Runtime / API
  - Python 3.12+
  - FastAPI
  - Pydantic settings
  - Uvicorn/Gunicorn for serving

Database
  - PostgreSQL
  - SQLAlchemy ORM
  - Alembic migrations

Security / Licensing
  - Ed25519-signed entitlement payloads
  - Server-side private signing key
  - Desktop app contains only the public verification key

Deployment
  - Docker image
  - AWS Lightsail instance for MVP
  - Docker Compose on Lightsail
  - PostgreSQL on the same instance for pilot, then Lightsail managed database when usage grows
  - HTTPS termination through Caddy or Nginx reverse proxy

Operations
  - CLI/admin script for no-portal license management
  - Structured logs
  - Database backups
  - Environment-variable based configuration
```

Rationale:

- The desktop app is already Python-based, so Python backend code keeps the stack small.
- FastAPI is enough for licensing, activation, entitlement, upload intake, and future AI API routing.
- PostgreSQL gives durable relational records for organizations, licenses, keys, devices, consents, upload jobs, and future billing/usage data.
- Alembic keeps schema changes versioned from the beginning.
- Signed entitlement payloads let the desktop app keep working offline without trusting mutable local JSON.
- Docker keeps deployment repeatable and makes later migration from Lightsail to ECS/Fargate simpler.

Do not start with:

- A full custom admin web portal before the licensing model is proven.
- A serverless-only architecture for license activation if it complicates database migrations and auditability.
- A no-server offline-only key generator if future subscription, AI usage, or revocation is expected.
- A heavyweight microservice split.

## 6. Server API Requirements

Initial API areas:

- auth callback/token integration through selected identity provider
- `/me`
- `/entitlements/current`
- `/devices/register`
- `/devices/{device_id}/validate`
- `/consents`
- `/uploads`
- `/uploads/{id}/parts`, if large-file multipart upload is used
- `/app/releases/latest`, if update checks are added

Desktop API client rules:

- Centralize all HTTP calls in one service layer.
- Use request timeouts.
- Use structured error types.
- Never log tokens.
- Never log full upload payloads by default.
- Include app version and device id in API requests.

## 7. Legal and Compliance Requirements

Production distribution should include the following documents or screens:

- End User License Agreement
- Terms of Service, if accounts or hosted services are provided
- Privacy Policy
- Training Data Consent
- Third-Party Open Source Notices
- Font license notice
- Tesseract/OCR dependency notice, if bundled or required

In-app legal surfaces:

- First-run consent screen
- Settings page showing current legal document versions
- Training-data upload opt-in/out controls
- Link or local copy for license/privacy documents

Data handling requirements:

- Explain what data stays local.
- Explain what data is uploaded.
- Explain whether uploaded data may be used for model or product improvement.
- Explain retention and deletion request handling.
- Explain customer responsibility for inspection video content.

## 8. Windows Packaging Requirements

Packaging should produce a repeatable Windows installer.

Recommended first packaging path:

- PyInstaller one-folder build for internal testing
- Inno Setup or WiX installer for external distribution
- Code signing certificate before broad distribution

Required packaged resources:

- Python runtime
- PySide6 runtime files
- OpenCV runtime files
- app source package
- `font/` Pretendard files
- `pipe.png`
- `pipe_template_calibration.json`
- legal documents
- optional Tesseract runtime, if bundled

Installer requirements:

- Install per-user by default unless enterprise deployment requires machine-wide install.
- Create Start Menu shortcut.
- Do not require admin rights for normal install if possible.
- Keep user workspaces outside the install directory.
- Preserve workspaces and settings on uninstall unless the user explicitly chooses removal.

## 9. Local Data and Security Requirements

Current local data:

- workspace SQLite database
- source video file paths
- capture images
- Excel/PDF report outputs
- error log
- app settings in user home

Production requirements:

- Do not store secrets in workspace files.
- Do not include sensitive metadata in `error.log`.
- Add a privacy mode for logs if server support asks users to send logs.
- Consider encrypting uploaded temporary files while queued.
- Consider SQLite encryption only if customer contracts require local at-rest protection.

## 10. App UX Requirements

Authentication UX:

- Show current login state in the app shell.
- Provide login, logout, and license status actions.
- Show clear messages for expired, offline grace, and revoked states.
- Do not interrupt active inspection work for non-critical validation failures.

Upload UX:

- Show upload consent state.
- Show upload queue status.
- Show failed uploads and retry action.
- Show whether full video upload is disabled, pending, or allowed.

Legal UX:

- First run must show required legal acceptance before account-based features.
- Optional training data consent must be separate from required terms.

## 11. Suggested Implementation Phases

### Phase 1 - Distribution Foundation

- Add app version source of truth.
- Add packaging spec.
- Add resource inclusion rules.
- Add legal document placeholders.
- Add license/privacy screen placeholders.

### Phase 2 - Auth and Entitlement

- Add auth service abstraction.
- Add secure credential storage.
- Add login/logout UI.
- Add device registration.
- Add entitlement validation and offline grace behavior.
- Add hidden license feature fields to entitlement payloads.
- Add hidden AI quota fields to entitlement payloads.
- Add internal CLI/admin commands for license key and AI quota management.

### Phase 3 - Upload Infrastructure

- Add upload consent UI.
- Add upload queue table.
- Add upload API client.
- Add defect-image and metadata upload.
- Add retry and status UI.

### Phase 4 - Production Hardening

- Add code signing.
- Add installer.
- Add update check or release channel policy.
- Add crash/log support policy.
- Add third-party notices.
- Add end-to-end packaging verification on Windows.

## 12. Open Decisions

- Which identity provider will be used?
- Will customers log in with email/password, SSO, or license key?
- Is offline use required for public-sector field environments?
- How many devices are allowed per account or license?
- Is full video upload allowed at all?
- Who owns uploaded training data?
- What retention period applies to uploaded data?
- Should local workspace data be encrypted?
- Will Windows installers be per-user or machine-wide?
- Will updates be automatic, manual, or managed by customer IT?
