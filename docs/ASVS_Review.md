# OWASP ASVS 4.0.3 Level 2 Review (V2, V3, V4, V13, V14)

Self-review of the web app against the chapters named in `docs/Web_Auth_Spec.md` §14 phase 7. Each row points at the code and the test that shows it. Status: **prepared, not signed off**. Sign-off needs a person accountable for the product, after the ZAP scan and penetration test on a deployed environment.

Legend: **Met** (implemented and tested), **Deviation** (accepted, see auth spec §13), **Open** (needs a deployed environment or an outside party).

## V2 Authentication

| ASVS | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 2.1.1-2.1.2 | Passwords 12-128 characters | Met | `security/passwords.py`; `tests/test_auth_api.py::test_password_policy` |
| 2.1.3-2.1.4 | No truncation; any Unicode, NFKC-normalized | Met | `normalize_password`; `test_security_units.py::test_password_hash_round_trip_with_nfkc` |
| 2.1.7 | Breached-password check | Met | `HibpChecker`, `OfflineListChecker`; `test_offline_breached_list` |
| 2.1.9 | No composition rules | Met | `validate_new_password` |
| 2.1.10 | No periodic rotation | Met | No rotation code |
| 2.2.1 | Anti-automation | Met | `security/throttle.py`, Cloud Armor (`infra/terraform/services.tf`); `test_delay_after_five_failures_then_lock_after_ten`, `test_unknown_email_is_throttled_like_a_real_account` |
| 2.2.3 | Notify on credential changes | Met | `password_changed`, `email_changed_notice`, `mfa_reset_notice` emails; `test_accounts_api.py` |
| 2.4.1, 2.4.4 | Passwords stored with Argon2id at or above OWASP minimums | Met | `pwdlib` Argon2id defaults; `test_password_hash_round_trip_with_nfkc` |
| 2.5.1-2.5.2 | Recovery secrets single use, no hints | Met | `password_resets`, `invitations` (hashed, single use, expiring); `test_password_reset_flow`, `test_invite_accept_and_login` |
| 2.5.4 | No shared or default accounts | Met | Invitation-only accounts; CLI bootstrap invites a named person |
| 2.5.6 | Reset does not reveal account existence | Met | Identical 202 response, email sent after commit; `test_reset_response_is_identical_for_unknown_email` |
| 2.5.7 | Reset does not bypass MFA | Met | Reset never logs in; `test_password_reset_flow` |
| 2.7/2.8 | OTP: time-based, single use, rate-limited | Met | `security/totp.py`, 5 attempts per challenge; `test_totp_login_and_replay_rejection`, `test_mfa_attempts_are_limited_per_challenge`, `test_password_login_does_not_reset_mfa_failures` |
| 2.8.4 | TOTP secret protected | Met | KMS (`KmsCipher`), AES-GCM locally; `test_local_cipher_round_trip_and_tamper_detection` |
| 2.9 | Recovery codes ≥ 80 bits, hashed | Met | `security/recovery_codes.py`; `test_recovery_codes_format_and_entropy`, `test_recovery_code_works_once` |

## V3 Session Management

| ASVS | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 3.1.1 | No session tokens in URLs | Met | Cookie only; token links use the URL fragment |
| 3.2.1 | New session on login | Met | `create_session`; `test_csrf_token_is_bound_to_session` |
| 3.2.2 | ≥ 64 bits entropy | Met | 256-bit tokens, stored as SHA-256; `test_session_token_is_stored_hashed` |
| 3.3.1 | Logout invalidates server-side | Met | `logout`; `test_state_change_requires_csrf_token` |
| 3.3.2 | Re-authentication after 12 h / 30 min idle | Deviation | 12 h absolute met; idle is 2 h (auth spec §13); `test_idle_timeout`, `test_activity_extends_idle_but_not_absolute` |
| 3.3.3 | Option to end other sessions after password change | Met | Automatic; `test_password_change_revokes_other_sessions` |
| 3.3.4 | View and log out active sessions | Met | `/api/auth/sessions`; `test_list_and_revoke_other_sessions` |
| 3.4.1-3.4.5 | Cookie Secure, HttpOnly, SameSite, `__Host-` prefix, Path=/ | Met | `test_session_cookie_attributes` |
| 3.5.x | Token-based sessions | N/A | Opaque server-side sessions |
| 3.7.1 | Re-authentication before sensitive actions | Met | `require_recent_authentication`; `test_email_change_requires_recent_authentication`, `test_project_delete_requires_recent_authentication` |

## V4 Access Control

| ASVS | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 4.1.1, 4.1.5 | Enforced server-side; fails closed | Met | `security/authorization.py` plus row-level security; `test_no_access_context_sees_nothing` |
| 4.1.2-4.1.3 | Least privilege; attributes not user-controllable | Met | Roles and organization from the session only; `test_user_cannot_create_project_for_someone_else` |
| 4.2.1 | No IDOR across tenants or roles | Met | `test_authorization_matrix.py` (every endpoint × 7 actors × private/shared), `test_row_level_security.py` |
| 4.2.2 | CSRF protection | Met | HMAC token + Origin + JSON-only; `test_state_change_requires_csrf_token`, `test_state_change_requires_exact_origin`, `test_state_change_requires_json` |
| 4.3.1 | MFA for administrative interfaces | Met | Admins must enrol; IAP in front of `/api/platform/*`; `test_admin_must_enroll_totp_before_first_session` |
| 4.3.2 | No directory browsing / metadata exposure | Met | No docs UI; private buckets; signed URLs |

## V13 API

| ASVS | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 13.1.1 | Consistent encoding/parsers | Met | Pydantic models for every body |
| 13.1.3 | No sensitive data in URLs | Met | Tokens in fragments and POST bodies only |
| 13.2.1 | Only intended HTTP methods | Met | Explicit FastAPI routes |
| 13.2.2 | Input validated against a schema | Met | Pydantic with length limits; `test_defect_classification_is_validated` |
| 13.2.3 | CSRF on REST endpoints | Met | See 4.2.2 |
| 13.2.5 | Content-Type checked | Met | Middleware; `test_form_body_is_rejected_even_without_json_header` |
| 13.2.6 | Headers and payloads not trusted | Met | Client IP only from trusted proxy hops; no `--proxy-headers` |

## V14 Configuration

| ASVS | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 14.1.x | Repeatable, automated builds and deploys | Met | Dockerfile, Cloud Build, Terraform, deploy by digest |
| 14.2.1-14.2.3 | Components current, unneeded features removed | Met | Lock files, Dependabot, `pip-audit`, `npm audit`; API image has no ffmpeg |
| 14.2.4 | Third-party assets from trusted sources | Met | No CDN scripts; bundled assets only |
| 14.3.x | No debug or version leaks | Met | `--no-server-header`; docs UI disabled; generic job errors (`test_invalid_file_is_rejected_and_deleted`) |
| 14.4.1-14.4.7 | Content-Type, CSP, nosniff, Referrer-Policy, frame-ancestors, HSTS | Met | `api/middleware.py`; `test_security_headers` |
| 14.5.3 | CORS allowlist | Met | API has no CORS; bucket CORS limited to the app origin |
| Secrets | No secrets in code or images | Met | Secret Manager, gitleaks in CI |
| Production safety | Dev settings cannot reach production | Met | `Settings` validator; `test_production_settings_require_real_secrets` |

## Open before sign-off

- OWASP ZAP baseline and authenticated scans against the deployed dev environment.
- External penetration test.
- Confirm the deployed headers, cookies and TLS policy (`MODERN`, TLS 1.2+) on the real domain.
- Legal: Privacy Policy and Terms (Web_Architecture §8), audit-log retention period, CSAP applicability.
- Accountable sign-off of this document and of the idle-timeout deviation.
