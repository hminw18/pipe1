# Tests

What each automated test protects, and why it exists. 207 Python test functions (143 server, 64 desktop/core) and 17 frontend tests.

## Running them

| Suite | Command | Needs |
|---|---|---|
| Server (`server/tests`) | `cd server && uv run pytest` | `PIPE1_TEST_ADMIN_URL` (Postgres superuser URL); without it the DB tests are skipped. ffmpeg for the media tests. |
| Desktop and core (`tests/`) | `pytest` from the repo root | `pip install -e ".[dev]"` |
| Frontend (`web/src/__tests__`) | `cd web && npm test` | Node 24 |

## What a test must earn

A test is kept when breaking the behaviour it checks would cause one of:

1. **A security or privacy hole**: authentication, sessions, CSRF, row-level security, authorization, signed URLs, untrusted media, account enumeration, Excel injection, license and update signatures.
2. **Wrong inspection output**: defect codes, grades and scores, unit grading, stop detection, depth reading, Excel/PDF content.
3. **Lost or corrupted data**: deletes, versions, replacing videos, migrations, uniqueness.
4. **A broken core flow** that a user would hit at once: invite and login, upload and capture, exports, email delivery.

Tests that only echo configuration, check framework behaviour, duplicate another test at a different layer, or check cosmetic details were removed on 2026-10-28; a second pass on 2026-10-29 cut the desktop licensing, update and packaging tests to their security checks and the stop-detection and PDF tests to their core behaviour (see the last section).

---

## Server (`server/tests`)

### Authentication and sessions: `test_auth_api.py`

| Test | Guarantees |
|---|---|
| `test_login_creates_session_and_me_returns_user` | Login starts a session and `/api/auth/me` returns the user. |
| `test_session_cookie_attributes` | Session cookie is `__Host-`, `Secure`, `HttpOnly`, `SameSite`. |
| `test_login_failures_are_indistinguishable` | Wrong password, unknown email and disabled accounts give the same response (no account enumeration). |
| `test_failed_login_is_audited` | Failed logins are written to the audit log. |
| `test_delay_after_five_failures_then_lock_after_ten` | Brute-force throttling schedule per account. |
| `test_unknown_email_is_throttled_like_a_real_account` | Throttling does not reveal whether an account exists. |
| `test_successful_login_clears_account_failures` | A real login resets the failure counter. |
| `test_state_change_requires_csrf_token` | POST/PATCH/DELETE without the CSRF header are refused. |
| `test_csrf_token_is_bound_to_session` | A CSRF token from another session is refused. |
| `test_state_change_requires_exact_origin` | Requests from other origins are refused. |
| `test_state_change_requires_json` | Form-encoded and `text/plain` bodies (sendable cross-site without preflight) are refused. |
| `test_security_headers` | CSP (including `blob:` media for local playback), HSTS and the other headers are set. |
| `test_idle_timeout` | Sessions end after 2 hours idle. |
| `test_activity_extends_idle_but_not_absolute` | Activity extends the idle timer but never past the 12-hour absolute limit. |
| `test_org_admin_signs_in_without_mfa_by_default` | Org admins are not forced into MFA. |
| `test_platform_admin_must_enroll_totp_before_first_session` | Platform admins get no session until TOTP is enrolled. |
| `test_totp_login_and_replay_rejection` | TOTP login works and a used code cannot be replayed. |
| `test_mfa_attempts_are_limited_per_challenge` | MFA guessing is capped per login challenge. |
| `test_password_login_does_not_reset_mfa_failures` | Re-entering the password cannot reset the MFA guess limit. |
| `test_challenge_expires` | The MFA step expires after 5 minutes. |
| `test_recovery_code_works_once` | Recovery codes are single use and case-insensitive. |
| `test_list_and_revoke_other_sessions` | Users can see and end their other sessions. |
| `test_cannot_revoke_someone_elses_session` | …but not anyone else's. |
| `test_reauthentication_updates_authenticated_at` | Re-entering the password unlocks sensitive actions for 10 minutes. |
| `test_reauthentication_requires_mfa_when_enrolled` | Re-authentication also asks for TOTP when enrolled. |
| `test_password_policy` | Too short, too long, and context-word passwords are refused. |
| `test_password_containing_email_local_part_is_rejected` | Passwords containing the email name are refused. |
| `test_password_change_revokes_other_sessions` | Changing the password logs out other sessions. |
| `test_wrong_current_password_blocks_change` | Password change needs the current password. |
| `test_session_token_is_stored_hashed` | Only a hash of the session token is stored. |
| `test_org_users_can_enable_and_disable_mfa` | Inspectors and org admins can turn MFA on and off. |
| `test_platform_admin_cannot_disable_mfa` | Platform admins cannot. |

### Accounts and administration: `test_accounts_api.py`

| Test | Guarantees |
|---|---|
| `test_invite_accept_and_login` | Invitation email → accept with password → login. |
| `test_invitation_expires` | Invitation links expire. |
| `test_resend_revokes_previous_invitation` | Re-sending an invite invalidates the old link. |
| `test_cannot_invite_existing_account` | No duplicate accounts by invitation. |
| `test_inspector_cannot_invite` | Only admins invite. |
| `test_invited_org_admin_signs_in_without_mfa` | An invited org admin is signed in directly. |
| `test_invited_platform_admin_must_enroll_mfa` | An invited platform admin must enrol TOTP first. |
| `test_bad_invitation_tokens_are_rate_limited_per_ip` | Token guessing is throttled. |
| `test_password_reset_flow` | Reset email → new password → old sessions revoked. |
| `test_reset_response_is_identical_for_unknown_email` | Reset does not reveal whether an account exists. |
| `test_new_reset_request_invalidates_older_token` | Only the latest reset link works. |
| `test_reset_token_expires` | Reset links expire. |
| `test_reset_requests_are_rate_limited_silently` | Reset spam is throttled without revealing it. |
| `test_password_change_invalidates_reset_tokens` | A password change kills outstanding reset links. |
| `test_email_change_flow` | Email change: confirm via the new address, notify the old one. |
| `test_email_change_requires_recent_authentication` | Email change needs re-authentication. |
| `test_email_change_to_taken_address_sends_nothing` | Changing to a taken address reveals nothing. |
| `test_deactivation_revokes_sessions_immediately` | Deactivated users are logged out at once. |
| `test_admin_cannot_touch_other_org_users` | Admin actions are limited to their own organization. |
| `test_role_change_requires_recent_auth_and_keeps_one_admin` | Role changes need re-auth; the last admin cannot be demoted. |
| `test_org_admin_resets_inspector_mfa_only` | Org admins reset inspectors' MFA, not other admins'. |
| `test_org_audit_log_is_scoped` | The audit log shows only the admin's organization. |
| `test_platform_admin_creates_organization_with_admin_invite` | Creating an organization invites its first admin. |
| `test_organization_deactivation_ends_member_sessions` | Deactivating an organization logs out all its members. |
| `test_platform_admin_cannot_use_org_admin_api` | Platform staff cannot use customer admin endpoints. |

### Authorization and row-level security

| Test | Guarantees |
|---|---|
| `test_authorization_matrix.py::test_authorization_matrix` | Every inspection endpoint × every kind of actor (owner, member, org admin, other org, platform admin) × private/shared project returns the status the permission table in `docs/Web_Auth_Spec.md` §2 requires. Written independently of the policy code. |
| `test_row_level_security.py` (20 tests) | The same rules enforced by PostgreSQL itself, connecting as `pipe1_app`: who sees which projects, who may add, edit, delete, change visibility or transfer ownership, that no context sees nothing, that the context does not leak to the next transaction, that platform admins see no project data, and that child rows cannot point at another project. This is the second line of defence if a route ever forgets `authorize()`. |

### Inspection data: `test_inspection_api.py`

| Test | Guarantees |
|---|---|
| `test_new_report_has_both_manholes_and_survey_defaults` | New reports start with both manholes and survey defaults. |
| `test_pipe_values_are_derived` | Undriven length and completion are calculated, not entered. |
| `test_partial_updates_leave_other_fields_alone` | PATCH changes only the fields sent. |
| `test_export_readiness` | Missing required fields are reported before export. |
| `test_report_number_unique_per_business` | Report numbers are unique within a business. |
| `test_duplicate_report_copies_inputs_with_new_number` | Duplicating copies inputs but not the video or defects. |
| `test_new_version_shares_video_and_copies_each_capture` | New versions share the video and get their own capture images. |
| `test_last_version_cannot_be_deleted` | A report always keeps one version. |
| `test_project_delete_removes_stored_files` | Deleting a project deletes its files. |
| `test_project_delete_requires_recent_authentication` | Project delete needs re-authentication. |
| `test_project_list_scope` | Project lists show only what the user may see. |
| `test_visibility_change_and_admin_access_are_audited` | Visibility changes and admin access to others' projects are audited. |

### Video, capture and jobs: `test_media_pipeline.py`

Uses real ffmpeg on small generated videos.

| Test | Guarantees |
|---|---|
| `test_upload_process_and_play` | Upload → processing → playable MP4 and thumbnail strip (re-encode path, MJPEG source). |
| `test_browser_playable_video_is_repackaged_not_reencoded` | H.264 sources are only repackaged (no re-encode) and come out seekable (index first). |
| `test_capture_is_frame_accurate` | Server-side capture returns the frame at the requested time. |
| `test_two_captures_at_same_time_have_separate_images` | Deleting one defect never deletes another's image. |
| `test_capture_without_distance_or_roi_is_rejected_before_saving` | A defect needs a distance; a refused capture stores nothing. |
| `test_defect_classification_is_validated` | Defect/condition item, category and grade combinations follow the taxonomy. |
| `test_replacing_video_keeps_defects` | Replacing the video keeps defects and deletes the old media. |
| `test_invalid_file_is_rejected_and_deleted` | Disguised files (e.g. playlists pointing at `/etc/passwd`) are refused and deleted. |
| `test_upload_limits` | Wrong file types and oversized uploads are refused. |
| `test_size_mismatch_is_rejected` | An upload that differs from the announced size is refused. |
| `test_tampered_or_oversized_upload_urls_are_refused` | Signed upload URLs cannot be redirected or overfilled. |
| `test_exports` | Excel and PDF exports are produced as jobs. |
| `test_comparison_requires_matching_reports` | Comparison PDFs need compatible reports. |
| `test_stop_analysis_job` | Stop analysis needs a depth region and then runs. |
| `test_quota_is_enforced` | Organization storage quota blocks new uploads. |
| `test_defects_can_be_captured_in_the_browser_while_uploading` | During upload, captures need a browser frame; that frame is stored, and the defect survives upload completion. |
| `test_unusable_browser_frames_are_rejected` | Non-PNG, truncated, or huge-dimension frames are refused before decoding. |
| `test_interrupted_jobs_are_resumed_at_startup` | On the dev VM, jobs cut off by a restart run again. |

### Depth counter reading: `test_depth_counter_reader.py`

No database needed. Protects the distances that stop analysis and captures fill in.

| Test | Guarantees |
|---|---|
| `test_server_reads_the_counter_with_the_text_recognizer` | The server uses the text recognizer, not the one-recorder digit templates (the model file and onnxruntime are present). |
| `test_reads_real_counter` (10 images) | Counters cut from a real inspection video read correctly: white and light backgrounds, smudged and blurred digits, and 7s and 3s that are misread as 9s and 8s without the black border added around the region. |
| `test_reads_other_counter_formats` | Other formats and fonts (`12.3`, `013.50m`, `128.0 M`, `0045.7m`) read correctly. |
| `test_text_that_is_not_one_number_is_not_a_distance` | Garbled readings become "unreadable", never a wrong distance. |
| `test_capture_distance_is_read_from_the_counter_region` | A capture's distance is read from the user's depth region of the frame. |

### Email and worker: `test_worker_and_email.py`

| Test | Guarantees |
|---|---|
| `test_worker_sends_with_valid_tasks_token` | The worker sends email for genuine Cloud Tasks requests. |
| `test_worker_rejects_unauthenticated_callers` | Anyone else gets 401/403. |
| `test_resend_payload_escapes_html` | User text in emails is HTML-escaped; From/To are correct. |
| `test_every_template_renders` | Every Korean template renders. |
| `test_mask_email` | Logs show masked addresses only. |
| `test_resend_rejection_is_permanent` | Messages Resend rejects (400/422) are not retried. |
| `test_resend_transient_or_config_errors_are_retryable` | Bad key, rate limit and outages are retried. |
| `test_background_queue_retries_transient_failures` | The dev VM email thread retries. |
| `test_background_queue_does_not_retry_rejected_messages` | …but not rejected messages. |
| `test_background_queue_does_not_block_the_caller` | Requests never wait for email (no timing leak). |
| `test_worker_drops_messages_the_provider_rejects` | Rejected messages are not retried forever by Cloud Tasks. |
| `test_incomplete_email_settings_fail_at_startup` | Missing key, localhost sender or unknown modes stop the server at startup. |

### Infrastructure

| Test | Guarantees |
|---|---|
| `test_after_commit.py` (2) | Work queued with `db.after_commit` sees committed rows and is skipped on rollback. |
| `test_migrations.py::test_migrated_schema_matches_models` | Migrations and models agree. |
| `test_migrations.py::test_row_level_security_enabled_on_all_project_tables` | No project table is missing RLS. |
| `test_migrations.py::test_app_role_cannot_modify_audit_history` | The app cannot edit or delete audit records. |
| `test_migrations.py::test_downgrade_and_upgrade_round_trip` | Every migration can be undone and redone. |
| `test_ids.py::test_uuid7_sorts_by_creation_time` | IDs sort by creation time (lists rely on it). |
| `test_security_units.py` (11) | Building blocks: TOTP window and replay, malformed codes, AES-GCM tamper detection, CSRF token binding, throttle schedule, password hashing with Unicode normalization, breached-password list, recovery code entropy, email normalization, production settings refuse dev secrets. |

---

## Desktop and core (`tests/`)

The desktop app remains shippable until the web app reaches parity, but its tests are kept to data safety and security. `pipe1_core` (taxonomy, grading, stop detection, Excel/PDF) is shared by both apps, so its tests protect the web app as well.

### Inspection rules (shared core)

| File | Guarantees |
|---|---|
| `test_defect_taxonomy.py` (5) | Defect and condition codes match the official code tables; grade/score tables (single-grade defects, pipe block = 대 only, 100 points). |
| `test_state_grading.py` (4) | Condition grade from score; unit grades use the maximum score per defect type with shared boundaries; reverse-direction ordering; stop conditions split units. |
| `test_report.py` (2) | Excel inspection workbook and multi-report business workbooks have the expected sheets and values. |
| `test_report_hardening.py` (4) | User text starting with `=` stays text (no formula injection); the report's own formulas still work; embedded images are downscaled; grades are not colour-coded. |
| `test_pdf_report.py` (5) | PDF captions label only the present item; memo is not mistaken for a defect; basic, missing-image and comparison reports are produced. |

### Stop detection and depth reading (shared core)

| File | Guarantees |
|---|---|
| `test_frame_candidate_detector.py` (2) | Candidate frames are found after motion bursts, at most the configured number per stop. |
| `test_depth_ocr_stop_detector.py` (4) | Digit slots of the depth counter are located (also with loose ROIs); depth series become stop segments with short gaps bridged; reading keyframes first finds exactly the stops that decoding every frame finds (including a short stop spanning a keyframe and an unreadable stretch), with far fewer frames decoded. |
| `test_depth.py` (3) | Distance OCR is positive on a real report video, falls back to Tesseract, returns nothing when both fail. |
| `test_video_capture.py` (1) | Opening a video falls back to the default backend when ffmpeg fails. |

### Desktop data and files

| File | Guarantees |
|---|---|
| `test_db.py` (13) | Connections are closed (Windows file locks); cascades on report delete; dynamic grade counts; condition items without grade; distance required; video replacement keeps defects; duplicating and versioning reports; unique business codes and report numbers; deleting the root version; pipe completion derived. |
| `test_artifact_deletion.py` (5) | Deleting reports, versions, businesses and projects deletes their files, and versions copy their captures. |
| `test_capture_save.py` (2) | Captures save to Unicode (Korean) paths; two captures of one frame get separate files. |
| `test_training_upload_service.py` (1) | Report generation queues and uploads a training snapshot when consented. |

### Licensing, updates and packaging

| File | Guarantees |
|---|---|
| `test_desktop_license_activation.py` (6) | Activation stores only a signed entitlement (not the raw key); deactivated licences are refused; expired offline grace is refused while the server is unreachable; entitlements for another device and tampered plaintext state or device IDs are not trusted. |
| `test_license_signing_contract.py` (2) | The desktop verifies the server's signed entitlement fixture and rejects a tampered one. |
| `test_license_runtime_config.py` (1) | Production builds require activation. |
| `test_client_network_security.py` (1) | Plain-HTTP external URLs are refused. |
| `test_update_service.py` (2) | Updates download only from the expected host and only when the SHA-256 matches. |
| `test_packaging_resources.py` (1) | Resources are found inside the PyInstaller bundle. |
---

## Frontend (`web/src/__tests__`)

| File | Guarantees |
|---|---|
| `client.test.ts` (4) | The CSRF token is sent only on state-changing requests; a 401 outside login ends the session; structured error codes are exposed; tokens are read from the URL fragment. |
| `timing.test.ts` (7) | Session warnings 2 minutes before the idle and absolute timeouts, expiry, and heartbeat only while the user is active and not too often. |
| `geometry.test.ts` (4) | Depth-region rectangles map correctly between the scaled, letterboxed player and video pixels, in both directions, including right-to-left drags and clamping. |
| `format.test.ts` (2) | Dates in Korea time; video timestamps to the millisecond. |

---

## Removed on 2026-10-28

| Removed | Why |
|---|---|
| `server/tests/test_healthz.py` | Checked that a constant endpoint returns a constant. |
| `test_ids.py`: version/variant bits, embedded milliseconds, uniqueness | Library-level details; sort order (the property the app relies on) is kept. |
| `test_auth_api.py`: email normalized at login | Duplicate of `test_email_normalization`. |
| `test_auth_api.py`: origin constant matches settings | Checked test configuration, not the app. |
| `test_auth_api.py`: form body without JSON header | Merged into `test_state_change_requires_json` (parametrized). |
| `test_auth_api.py`: taxonomy lists scores | API echo of core taxonomy data, covered by `test_defect_taxonomy.py`. |
| `test_security_units.py`: password length bounds | Duplicate of `test_password_policy`. |
| `test_accounts_api.py`: platform invitation role | Covered by `test_invited_platform_admin_must_enroll_mfa`. |
| `test_worker_and_email.py`: unknown template, successful send, give-up after last retry, settings accepted | Trivial or implied by the remaining queue and error-classification tests. |
| `test_media_pipeline.py`: probe reports playability | Covered by the repackaging test. |
| `test_media_pipeline.py`: depth ROI must fit | Minor input validation with no security or data impact. |
| `test_inspection_api.py`: explicit visibility | Schema validation already enforced by the request model. |
| `test_db.py`: old schema migrations (2), performance indexes, recreating a deleted workspace, Korea-time helper | No installed desktop users with old data exist; the rest checked implementation details. |
| `test_packaging_resources.py`: installer text styles, logo lookup | Cosmetic; resource lookup is covered by the bundle-root test. |
| `test_app_license_startup.py`: prompt when not suppressed | Implied by "prompts again for next version". |
| `test_license_runtime_config.py`: production ready, update override | Covered by the env-file test; echo of configuration. |
| `test_client_network_security.py`: non-object payload | Same code path as the malformed-payload test. |
| `test_desktop_license_activation.py`: DPAPI round trip, forced device ID | Windows-only API smoke test; minor variant of the protected-ID test. |
| `tests/test_training_consent_store.py` | Toggled a boolean in a file. |

## Removed on 2026-10-29 (second pass)

| Removed | Why |
|---|---|
| `tests/test_app_license_startup.py` (6) | Qt dialog flow around the licence gate; the gate's decisions are covered by the activation tests, and the file needed PySide6 to run at all. |
| `tests/test_stop_detector.py` (3) | Tested `StopSegmentDetector`, which neither app uses (the pipeline uses the candidate and depth detectors). |
| `test_desktop_license_activation.py`: online refresh, inactive entitlement cleared, malformed response, offline grace allowed and refreshed, random device ID stored | Normal-path and convenience behaviour; the refusals that protect the licence are kept. |
| `test_license_runtime_config.py`: dev optional, explicit flag, env file loading, OS override | Configuration plumbing. |
| `test_client_network_security.py`: malformed payload | Error-message classification, no security effect. |
| `test_update_service.py`: non-newer release, size required, helper launch, passive msiexec | Update convenience; host and hash checks are kept. |
| `test_packaging_resources.py`: licence env lookup, WiX fonts and same-version upgrade | Installer details, checked when building the MSI. |
| `test_frame_candidate_detector.py` (6), `test_depth_ocr_stop_detector.py` (3), `test_depth.py` (1), `test_video_capture.py` (2) | Pinned tuning constants and internal helpers; a tuning change would fail them without anything being wrong. |
| `test_pdf_report.py`: connection line geometry (2), comparison page grouping | Layout geometry. |
