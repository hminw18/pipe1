# Pipe1 Web Architecture

Target architecture and migration plan for moving Pipe1 from the PySide6 desktop app to a multi-user web app on Google Cloud. Product behavior is defined in `docs/Product_Requirements.md`; login and access control in `docs/Web_Auth_Spec.md`.

Status: planning. No real users or license holders exist yet, so no customer data or license migration is required.

## 1. Decisions

| Topic | Decision |
| --- | --- |
| Backend | Python + FastAPI, reusing the Qt-free core: `db.py` logic, `services/`, `stop_detection/`, `defect_taxonomy.py`, `state_grading.py` |
| Frontend | React + TypeScript; API types generated from FastAPI's OpenAPI schema |
| Cloud | Google Cloud, region `asia-northeast3` (Seoul) |
| Database | Cloud SQL for PostgreSQL, SQLAlchemy + Alembic |
| Files | Cloud Storage (private bucket) |
| Login | Email + password, admin-issued accounts (see `docs/Web_Auth_Spec.md`) |
| Email | SendGrid |
| Not ported | `licensing/`, `updates/`, DPAPI, device identity, PyInstaller/WiX packaging |

Why Python stays: the heavy work runs in native code (OpenCV/numpy, ffmpeg, PostgreSQL), the PDF layout and detectors are already tuned, and future AI detection will be Python. CPU-heavy jobs run in separate worker processes, not in the API process.

## 2. Target Architecture

```text
Browser (React + TS)
   │  same origin: https://app.<domain>/      → static frontend
   │                https://app.<domain>/api  → API
   ▼
External HTTPS Load Balancer + Cloud Armor (WAF, per-IP rate limits)
   ├── Backend bucket + Cloud CDN  → React build
   └── Serverless NEG → Cloud Run "api" (FastAPI)
                          ├── Cloud SQL PostgreSQL (private IP)
                          ├── Cloud Storage (videos, captures, reports)
                          ├── Secret Manager / Cloud KMS
                          └── Cloud Tasks ──► Cloud Run Jobs / "worker" service
                                               ├── video transcode (ffmpeg)
                                               ├── stop detection (OpenCV)
                                               ├── Excel/PDF generation
                                               └── email sending (SendGrid)
```

## 3. GCP Components

| Need | Service | Notes |
| --- | --- | --- |
| API | Cloud Run | Stateless; sessions live in PostgreSQL. Request bodies are limited (32 MB on HTTP/1), so videos never pass through the API. |
| Background work | Cloud Run Jobs or a worker service, triggered by Cloud Tasks | Larger CPU/memory than the API. Compute Engine only if GPUs are needed later. |
| Database | Cloud SQL for PostgreSQL | Private IP, IAM database auth, automated backups, point-in-time recovery. Row-level security enabled (see auth spec). |
| Object storage | Cloud Storage | Uniform bucket-level access, public access prevention. V4 signed URLs for download; signed resumable uploads for videos. |
| Edge | External HTTPS Load Balancer, Cloud CDN, Cloud Armor | One origin for frontend and API so cookies need no CORS. OWASP CRS rules and rate limits on `/api/auth/*`. |
| Secrets | Secret Manager, Cloud KMS | DB credentials, SendGrid API key, CSRF/session config; KMS key encrypts stored TOTP secrets. |
| Platform admin area | Identity-Aware Proxy | Extra Google-identity gate in front of the platform-admin UI. |
| Observability | Cloud Logging, Error Reporting, Cloud Monitoring | Structured JSON logs; alerts on failed-login spikes, job failures, 5xx rate. |
| CI/CD | Cloud Build or GitHub Actions, Artifact Registry | Workload Identity Federation (no service-account JSON keys); one least-privilege service account per Cloud Run service. |
| Infrastructure | Terraform | All of the above declared as code. |

Check before committing to GCP for public-sector customers: whether they require a CSAP-certified cloud and whether the chosen GCP region qualifies.

## 4. Video Pipeline

1. **Upload**: the browser requests an upload session from the API (after access check), then uploads the video directly to Cloud Storage with a signed resumable upload. Multi-GB files and flaky connections are expected.
2. **Transcode**: a worker job converts the original (`.avi`, `.mkv`, `.mov`, `.mp4`) to H.264 MP4 for browser playback and builds a timeline thumbnail strip. The original is kept.
3. **Playback**: HTML5 `<video>` with a short-lived signed URL. Frame stepping uses `requestVideoFrameCallback` where available.
4. **Capture**: the browser sends `timestamp_ms`; the server extracts the full-resolution frame from the original file with OpenCV and stores it. Browser-side frame extraction is not used for the saved image, so captures stay identical to desktop quality and are frame-accurate.
5. **Stop detection**: runs as a background job; the UI shows progress by polling or server-sent events.

Video timestamps must be derived consistently between playback, capture, and analysis (same decoder backend, time-based rather than frame-index-based seeking) so defect markers and stop segments line up.

## 5. Data Layer

- PostgreSQL schema ported from `db.py`, managed with Alembic migrations (versioned, run once per deploy, never on every request or startup).
- Every project carries `organization_id`, `owner_user_id`, `visibility`; child rows inherit access through their project (see auth spec).
- All timestamps stored as UTC `timestamptz`; convert to KST only for display. (The desktop DB mixes KST `updated_at` with UTC `created_at`.)
- Files are referenced by storage object keys, never absolute paths. Object keys: `org/{org_id}/project/{project_id}/report/{report_id}/...`.
- Update endpoints apply partial updates (PATCH semantics); fields not sent are left unchanged.
- IDs exposed in URLs are UUIDv7.

## 6. Fixes to Carry Into the Port

Issues found in the desktop code that must not be reproduced in the web backend:

| Issue | Desktop location | Web requirement |
| --- | --- | --- |
| Two defects captured at the same timestamp share one image file; deleting one deletes the other's image | `services/storage_service.py` `capture_path` | Unique object key per defect |
| Capture image written before distance validation, leaving orphans on failure | `services/inspection_service.py` `capture_and_save_defect` | Validate first, then write |
| Detectors decode every frame while sampling at 1-5 fps | `stop_detection/depth_ocr_detector.py`, `frame_candidate_detector.py` | `grab()` skipped frames, `retrieve()` sampled ones |
| Player and analysis use different OpenCV backends / frame-index seeking | `ui/main_window.py`, `stop_detection/video_capture.py` | One shared time-based decode path |
| User text starting with `=` becomes an Excel formula | `services/report_service.py` | Write user text as literal strings |
| Full-resolution PNGs embedded in Excel/PDF | `services/report_service.py`, `pdf_report_service.py` | Embed downscaled JPEG thumbnails |
| Partial update payload sets missing fields to NULL | `db.py` `update_report`, `update_defect` | PATCH semantics |
| File deletes trust paths stored in the DB | `services/inspection_service.py` | Only delete keys under the resource's own prefix |
| Migrations run on every startup | `db.py` `_run_migrations` | Alembic, versioned |
| Stop analysis logic lives in the UI module | `ui/main_window.py` `StopAnalysisWorker` | Move into `stop_detection` / a service before porting |

## 7. Migration Phases

1. **Extract the core (desktop stays shippable)**: move non-UI logic out of `ui/main_window.py` into services; put storage behind an interface (local disk / Cloud Storage); fix the items in section 6 that live in shared code.
2. **Infrastructure and auth**: Terraform for GCP; PostgreSQL schema + Alembic; auth phases 1-2 from `docs/Web_Auth_Spec.md`.
3. **API**: Project/Business/Report CRUD, video upload/transcode, capture, defects, stop detection jobs, exports; every route behind the authorization layer.
4. **Frontend**: navigation → report forms → video player + capture → defect list → export.
5. **Hardening and launch**: remaining auth phases, ASVS review, external penetration test, legal documents (section 8).

The desktop app remains available until the web app reaches feature parity.

## 8. Legal and Compliance Checklist

Required before the first real customer:

- Terms of Service
- Privacy Policy (개인정보 처리방침) under the Korean Personal Information Protection Act
- Training-data consent text (if training upload is enabled; see section 9)
- Third-party open-source notices
- Font license notice (Pretendard)

In-app surfaces:

- Links to Terms and Privacy Policy on the login page and in settings
- Training-data opt-in/out control for org admins
- Current legal document versions visible in settings

The Privacy Policy must explain what data is stored, where (GCP Seoul), how long it is retained, how deletion requests are handled, and that customers are responsible for the content of inspection videos they upload.

## 9. Training Data Policy (Summary)

Kept from the desktop training-upload requirements; the implementation will be server-side because data already lives on the server.

- Training use requires explicit consent, separate from account creation and the Terms of Service. Consent is recorded per organization with type, version, accepted/revoked timestamps, and the user who changed it.
- Consent levels: `none`; `capture_images_and_labels` (default when enabled); `extended_report_context` (requires separate consent text).
- Default training payload: capture image, defect/condition labels, item category, pipe type, category, specification, drive direction, distance, timestamp, grade, quadrant.
- Never included by default: source videos, generated Excel/PDF files, project/business/client names, road address, lot number, manhole coordinates, inspector/contractor names, freeform memo.
- Revocation stops future use; deletion of already-copied data follows the Privacy Policy.
- Training copies live in a separate bucket/dataset with its own access controls and no customer-identifying fields.
