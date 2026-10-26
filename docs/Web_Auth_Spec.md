# Pipe1 Web Authentication and Access Control Spec

Login, session, and authorization design for the Pipe1 web app. Platform architecture is in `docs/Web_Architecture.md`.

Baseline standards: OWASP ASVS 4.0.3 Level 2 (V2 Authentication, V3 Session Management, V4 Access Control) and NIST SP 800-63B for password rules. Accepted deviations are listed in section 13.

## 1. Decisions

| Topic | Decision |
| --- | --- |
| Login ID | Email address |
| Account creation | Admin-issued via email invitation; no self sign-up |
| Organizations | Users belong to one organization; inspectors in the same org can see each other's org-visible projects |
| Project visibility | `private` or `org`, set by owner |
| Org admin access | Can view and edit private projects in their org; can transfer ownership |
| Org member edit rights | Can edit org-visible projects; delete and visibility change stay with owner and org admin |
| Session timeouts | Idle 2 hours, absolute 12 hours |
| MFA | TOTP, required for `org_admin` and `platform_admin`, optional for inspectors |
| Email | SendGrid |
| Implementation | In-house in FastAPI with vetted libraries: `pwdlib[argon2]`, `pyotp`, `secrets` |

## 2. Roles and Permissions

| Role | Who | Capabilities |
| --- | --- | --- |
| `platform_admin` | Pipe1 operator staff | Create organizations and their first org admin; deactivate organizations. Separate admin area behind IAP. MFA required. |
| `org_admin` | Customer manager | Invite/deactivate users in own org, change roles, reset a user's MFA, transfer project ownership, view org audit log. MFA required. |
| `inspector` | Field user | Create projects, set visibility, work on accessible projects. |

Project permission matrix (all access is confined to the user's own organization):

| Action | Owner | Org member, `org` project | Org member, `private` project | Org admin |
| --- | --- | --- | --- | --- |
| View | yes | yes | no | yes |
| Edit businesses/reports/videos/defects, export | yes | yes | no | yes |
| Change visibility | yes | no | no | yes |
| Delete project | yes | no | no | yes |
| Transfer ownership | no | no | no | yes |

Rules:

- Businesses, reports, videos, defects, captures, and exports inherit access from their project.
- Users outside the organization get `404` for every resource, never `403`, so existence is not revealed.
- Every org-admin read or write on a project they do not own is recorded in the audit log.
- UI copy for private: `비공개 (본인 및 관리자만)`.

## 3. Data Model

```text
organizations(id uuid, name, status[active|disabled], created_at)
users(id uuid, organization_id, email citext unique, role, status[invited|active|disabled],
      mfa_required bool, created_at, last_login_at)
password_credentials(user_id pk, hash, updated_at)
mfa_totp(user_id pk, secret_ciphertext, confirmed_at, last_used_step)
recovery_codes(id, user_id, code_hash, used_at)
sessions(id, user_id, token_hash unique, csrf_token_hash, created_at, last_seen_at,
         idle_expires_at, absolute_expires_at, ip, user_agent, revoked_at)
invitations(id, organization_id, email, role, token_hash unique, invited_by, expires_at, used_at, revoked_at)
password_resets(id, user_id, token_hash unique, expires_at, used_at)
email_changes(id, user_id, new_email, token_hash unique, expires_at, used_at)
login_throttle(key, failure_count, window_started_at, locked_until)
audit_events(id, organization_id, actor_user_id, action, target_type, target_id,
             ip, user_agent, created_at, metadata jsonb)

inspection_projects(+ organization_id, owner_user_id, visibility[private|org])
```

Rules:

- IDs are UUIDv7.
- Emails are trimmed, NFKC-normalized, and lowercased before storage and lookup; uniqueness is case-insensitive.
- Every token (session, CSRF, invitation, reset, email change, recovery code) is generated with `secrets.token_urlsafe(32)` or stronger and stored only as a SHA-256 hash.
- TOTP secrets are encrypted with a Cloud KMS key before storage.
- Credentials live in their own table so OIDC/SAML SSO can be added later without changing `users`.
- Users are deactivated, never hard-deleted, to keep audit history intact.

## 4. Passwords

- Length: minimum 12 characters, maximum 128. Spaces and all Unicode allowed; NFKC-normalized before hashing.
- No composition rules and no periodic forced rotation.
- Rejected: passwords in a breached-password corpus (HIBP k-anonymity range API or bundled offline list), and passwords containing the user's email local part or `pipe1`.
- Hashing: Argon2id via `pwdlib`, parameters at or above OWASP minimums (m=19 MiB, t=2, p=1). Rehash on successful login when parameters change.
- Password change requires the current password, revokes all other sessions, and sends a notification email.
- Passwords are never logged, emailed, or visible to admins.

## 5. Login

Endpoint: `POST /api/auth/login` with `{email, password}`.

1. Apply throttling (section 8) before any credential work.
2. Look up the user. If not found, still verify against a fixed dummy Argon2id hash so response time does not reveal account existence.
3. On any failure (unknown email, wrong password, invited, disabled, disabled org) return the same `401` and message: `이메일 또는 비밀번호가 올바르지 않습니다.`
4. If the user requires MFA, return `{mfa_required: true}` and set a short-lived pre-auth cookie (5 minutes, single use, not a session). `POST /api/auth/mfa` accepts a TOTP code or a recovery code. TOTP codes are rejected if their time step was already used.
5. On success: create a new session (never reuse a pre-login identifier), set cookies, update `last_login_at`, write an audit event, and rehash the password if needed.

## 6. Sessions

- Opaque random token in cookie `__Host-pipe1_session` with `HttpOnly; Secure; SameSite=Lax; Path=/`. No tokens in localStorage, sessionStorage, or URLs.
- Stored server-side in `sessions` (Cloud SQL). Cloud Run instances hold no session state.
- Idle timeout: 2 hours since the last authenticated request or heartbeat.
- Absolute timeout: 12 hours since login, regardless of activity.
- Heartbeat: the frontend calls `POST /api/auth/heartbeat` every 5 minutes while the tab is in active use or a video is playing, so long video reviews are not counted as idle.
- Warning: the frontend shows a dialog 2 minutes before either timeout. `계속 작업` resets only the idle timer, never the absolute timer.
- Expiry: the frontend shows an in-place re-login dialog without a page reload. Report autosave ensures no work is lost.
- Revocation: logout deletes the session server-side. Password change/reset, deactivation, role change, MFA reset, and org deactivation revoke all of the user's sessions immediately.
- Users can list their active sessions and revoke any or all (`로그아웃 (모든 기기)`).
- Re-authentication (password, plus MFA if enrolled, within the last 10 minutes) is required for: email change, MFA disable, project delete, ownership transfer, role change.
- Timeouts are configurable per deployment: `SESSION_IDLE_TIMEOUT_SECONDS=7200`, `SESSION_ABSOLUTE_TIMEOUT_SECONDS=43200`.
- No cap on concurrent sessions per user.

## 7. CSRF and Browser Security

- Frontend and API are served from the same origin (`/` and `/api`); CORS is not enabled.
- Every state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) must:
  - carry an `Origin` header matching the app origin, and
  - carry `X-CSRF-Token` equal to the session's CSRF token (issued at login, readable by the frontend through `GET /api/auth/me`, stored hashed server-side).
- Response headers:
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains`
  - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'` (no inline scripts)
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Cache-Control: no-store` on all `/api/auth/*` and authenticated API responses

## 8. Throttling

- Per account: failures 5-10 within 15 minutes add an increasing delay; after 10, the account is temporarily locked for 15 minutes. Locks expire automatically; there is no permanent lockout.
- Per IP: Cloud Armor rate limits on `/api/auth/*`, plus an application-level per-IP counter.
- Password reset and invitation-accept endpoints are rate-limited per email and per IP.
- Lockouts and throttle events are audited; spikes trigger a Cloud Monitoring alert.

## 9. Account Lifecycle

### 9.1 Organization onboarding

1. `platform_admin` creates the organization and invites its first `org_admin`.
2. The org admin accepts the invitation, sets a password, and enrolls TOTP before first access.

### 9.2 Invitation

1. Org admin submits email and role.
2. Server creates an invitation token (single use, expires in 72 hours) and queues the invite email.
3. The invitee opens the link, sets a password (section 4); admins must also enroll TOTP.
4. The user becomes `active`; the invitation is marked used.
5. Re-sending an invitation revokes the previous token. Inviting an email that already has an account is rejected with a generic message to the admin.

### 9.3 Password reset

1. `POST /api/auth/password-reset` always returns the same response: `계정이 존재하면 재설정 메일이 발송됩니다.`
2. Token is single use and expires in 30 minutes; requesting a new one invalidates older ones.
3. Completing the reset sets the new password, revokes all sessions, and sends a confirmation email. It does not log the user in automatically when MFA is enrolled.

### 9.4 Email change

Requires re-authentication, a confirmation link sent to the new address (single use, 30 minutes), and a notification to the old address.

### 9.5 MFA

- Enrollment: show QR code and secret, require one valid code to confirm, then issue 10 recovery codes (shown once, stored hashed).
- Lost device: org admin resets the user's MFA (audited, sessions revoked); the user re-enrolls at next login. Platform admins reset org admins.
- Passkeys (WebAuthn) may be added later as an additional factor.

### 9.6 Deactivation

- Takes effect immediately and revokes all sessions.
- The user's projects remain; org admin can transfer ownership.
- Deactivated users cannot log in, reset passwords, or accept invitations.

## 10. Authorization Layer

1. A single FastAPI dependency (`current_user`) resolves the session cookie, enforces timeouts, verifies CSRF on state-changing methods, and loads the user and organization. Routes never read the session themselves.
2. A single policy module implements the matrix in section 2: `authorize(user, project, action)`.
3. Every route resolves its target up to the owning project (defect → report → business → project) and calls `authorize` before any read or write. No route fetches a row by bare ID without this.
4. List endpoints use one scoped query helper that always applies:
   `organization_id = :org_id AND (visibility = 'org' OR owner_user_id = :user_id OR :is_org_admin)`.
5. PostgreSQL row-level security on `inspection_projects` and all child tables, keyed by `app.org_id` / `app.user_id` / `app.role` set per transaction, enforces organization isolation even if application code misses a check.
6. Files: private bucket; object keys under `org/{org_id}/project/{project_id}/...`; downloads use V4 signed URLs valid for 5 minutes, issued only after `authorize`; uploads use signed resumable sessions bound to a server-chosen key. Clients never supply storage paths.
7. Background jobs carry the requesting user and organization and re-check authorization when they run.

## 11. Email (SendGrid)

- Sender domain authenticated in SendGrid (SPF, DKIM, custom return path); DMARC record published, starting at `p=quarantine`.
- Restricted API key with only Mail Send permission, stored in Secret Manager, readable only by the worker service account.
- Click tracking and open tracking disabled for all auth emails so token links are not rewritten through a third-party domain.
- Dynamic templates (Korean): invitation, password reset, password changed, email-change confirmation, email-changed notice to old address, MFA reset notice, optional new-device login notice.
- Emails are sent from a Cloud Tasks job, not inside the HTTP request, so provider latency or outages do not affect login responses or reveal account existence.
- If the Event Webhook is used, its signature is verified; bounced invitations are shown to the org admin.
- Development and CI use sandbox mode or a fake mail backend.

## 12. Audit and Monitoring

Audited actions: login success/failure, logout, lockout, MFA enroll/reset/disable, password change/reset, email change, invitation create/resend/accept/revoke, role change, deactivation, session revoke, project visibility change, project delete, ownership transfer, org-admin access to non-owned projects, exports.

- Logs never contain passwords, tokens, session IDs, TOTP secrets, or recovery codes.
- Org admins can view their organization's audit log; platform admins can view all.
- Alerts: failed-login spikes per IP or account, any `platform_admin` action, elevated `401/403/404` rates on resource endpoints.

## 13. Accepted Deviations

| Requirement | Standard | Pipe1 | Reason |
| --- | --- | --- | --- |
| Idle session timeout | ASVS 4.0.3 V3.3.2 (L2): 30 minutes | 2 hours | Long video review sessions. Compensated by 12-hour absolute timeout, heartbeat-based activity, MFA for admins, session list with global logout, Cloud Armor rate limiting, and audit logging. |

## 14. Implementation Phases

| Phase | Deliverable | Exit criteria |
| --- | --- | --- |
| 1 | Schema and Alembic migrations, RLS policies, project ownership/visibility columns | Migrations apply cleanly; RLS denies cross-org reads in tests |
| 2 | Auth core: hashing, login/logout, sessions with timeouts and heartbeat, CSRF, throttling, security headers | Unit tests incl. timing-equal failures, throttling, timeout expiry |
| 3 | Invitation, password reset, email change via SendGrid worker | End-to-end tests for single-use and expiring tokens |
| 4 | Authorization layer, scoped queries, signed URLs, visibility UI | Authorization matrix test suite passes |
| 5 | TOTP MFA with recovery codes; enforced for admins | Enrollment, login, recovery, admin reset tested |
| 6 | Platform-admin and org-admin screens, audit log view, active-session list | Manual QA |
| 7 | Hardening: ASVS L2 review of V2/V3/V4/V13/V14, `pip-audit` and `npm audit` in CI, OWASP ZAP scan, external penetration test before first paying customer | Checklist signed off |

### Authorization matrix test suite

For every API endpoint, run as: owner; same-org member on an `org` project; same-org member on a `private` project; org admin; user from another org; deactivated user; no session. Assert the expected status for each. This suite runs in CI and blocks merges on failure.
