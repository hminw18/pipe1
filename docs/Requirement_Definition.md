# SewerPipe Inspector Desktop - Requirement Definition

## 1. Product Definition

- Product: SewerPipe Inspector Desktop
- Platform: Windows 10/11 desktop
- Runtime model: single-user, offline, local-only
- Primary purpose: sewer/manhole CCTV inspection, report-level defect capture, condition record management, and report export

## 2. Current Change Objective

The application navigation and data ownership model must change from:

```text
Project
 └── Zone
     └── Pipe
         └── Video
             └── Defect
```

to:

```text
Project
 └── Business
     └── Report
         ├── Video
         ├── Pipe Information
         │   └── Actual Survey Information
         └── Defect
```

Key rule:

- Video playback, depth/position recognition, and defect capture must belong to a selected `Report`.
- A single `Report` has exactly one video.
- Existing database migration is not required for this change.

## 3. Scope Definition

### In Scope

- Project, Business, and Report lifecycle management
- Report-level video assignment
- Video playback with timeline navigation and frame stepping
- Manual defect capture while reviewing a report video
- Defect list management for each report
- Report-level Excel output using the new Project/Business/Report/Pipe/Defect model
- PDF visual report output with limited defect fields
- SQLite-backed local data persistence
- Local filesystem storage for captures and reports

### Out of Scope

- Migration of existing `zones`, `pipes`, `videos`, and `defects` data to the new model
- Cloud sync or server backend
- Multi-user roles and authentication
- GIS integration beyond manually entered location/address/manhole coordinates

## 4. Stack and Dependencies

- Python 3.11+
- PySide6 for desktop UI
- OpenCV for video decode and frame extraction
- SQLite for local relational data
- openpyxl for Excel report output
- pytesseract for optional depth/distance OCR
- reportlab for PDF visual report output

## 5. Navigation Model

The left navigation panel must show three independent vertical list sections:

```text
[Project section]
  Project A
  Project B

[Business section]
  Business 1
  Business 2

[Report section]
  Report 1
  Report 2
```

Display rules:

- Project list item label: `{project_name}`
- Business list item label: `{business_code} - {business_name}`
- Report list item label: `{report_number} / {pipe_number}`
- The desktop UI should visually follow a SaaS-style admin console for the `Pipe1` brand: dark navy sidebar, right-side page title area, breadcrumb trail, modern tables, and clear CTA hierarchy.
- Project, Business, and Report must occupy separate boxed sections, not horizontal table columns and not only indented tree rows.
- Items inside each Project/Business/Report section are shown as bullet-style list entries, not boxed item cards.
- Videos must not appear in the left navigation.
- When nothing is selected, the right panel shows the Project list.
- Selecting a Project shows that Project's Business list in the right panel.
- Selecting a Business shows that Business's Report list in the right panel.
- Selecting a Report opens the report workspace where video review, report details, pipe information, and defects are managed.
- The right panel must provide lifecycle actions for the visible entity level, including create, duplicate, edit, and delete where applicable.
- Primary create actions must be visually emphasized; open/view, edit, duplicate, and delete actions are displayed as adjacent action buttons in the page card action bar.
- Table headers and read-only table items are center-aligned.
- Tables should not be wrapped by an additional outer card/box solely for decoration.
- Table column default widths should reflect field type: codes, dates, statuses, and counts remain compact; names, descriptions, memo, and image/path fields receive wider columns.
- Report-detail table input controls should use compact widths and enough row height to prevent text clipping.
- The left navigation is selection-only. Create, duplicate, edit, and delete actions are handled in the right panel.
- Duplicating a Report copies report input data, pipe/manhole information, and actual survey information, but does not copy the Report video or defect records.

## 6. Data Model

### 6.1 Project

A Project contains multiple Business records.

Fields:

- `id`
- `project_name`
- `created_at`

Rules:

- `created_at` is generated automatically by the system.
- Project creation/editing UI only needs to collect the project name.

### 6.2 Business

A Business belongs to one Project and contains multiple Reports.

Fields:

- `id`
- `project_id`
- `business_code`
- `business_name`
- `client`
- `business_start_date`
- `business_end_date`

Rules:

- `business_start_date` and `business_end_date` are string fields for now.
- Business creation/editing should use a dialog.

### 6.3 Report

A Report belongs to one Business and owns one video, one pipe-information record, and multiple defects.

Fields:

- `id`
- `business_id`
- `report_number`
- `pipe_number`
- `survey_purpose`
- `survey_date`
- `buried_years`
- `inspector`
- `contractor`
- `treatment_area`
- `drainage_area`
- `drainage_district`
- `drain_type`
- `drain_system`
- `province`
- `city_county`
- `town`
- `village`
- `lot_number`
- `road_address`
- `pipe_type`
- `category`
- `specification`
- `created_at`

Rules:

- `survey_date` is a string field for now.
- `specification` replaces the old `diameter` concept.
- Report creation should use a lightweight dialog for minimal identity fields such as `report_number` and `pipe_number`.
- After creation, the selected Report opens a detail page where the remaining report, pipe, manhole, video, and defect data can be entered.
- Report details must be editable on a report detail page, not only in a creation dialog.
- Saving incomplete report details is allowed.
- Export actions must be disabled until required export fields are complete.

### 6.4 Pipe Information

Pipe Information belongs to one Report.

Fields:

- `id`
- `report_id`
- `upstream_manhole_id`
- `downstream_manhole_id`
- `length_m`
- `total_drive_distance_m`
- `is_completed`
- `undriven_distance_m`

Rules:

- `length_m` replaces the old `Pipe.length` concept.
- `undriven_distance_m = length_m - total_drive_distance_m`.
- `is_completed` is derived automatically: complete when `length_m == total_drive_distance_m`, incomplete otherwise.
- `is_completed` and `undriven_distance_m` should not be hand-entered when both source values are available.
- The UI may display and persist derived values for export convenience, but source values remain `length_m` and `total_drive_distance_m`.
- In the report detail page and Excel output, Pipe Information and upstream/downstream Manhole Information must be displayed as one combined table.

### 6.5 Manhole

Pipe Information has two manholes:

- Upstream manhole
- Downstream manhole

Each manhole has the same optional fields:

- `manhole_number`
- `manhole_type`
- `internal_material`
- `cover_material`
- `el_m`
- `size`
- `depth_m`
- `invert`
- `ladder_shape`
- `latitude`
- `longitude`

Rules:

- Upstream/downstream manhole details are optional.
- Empty manhole fields must not block saving a Report.
- In the combined pipe/manhole table, row 1 is the upstream manhole and row 2 is the downstream manhole.
- Row 3 displays `length_m`, `total_drive_distance_m`, `is_completed`, and `undriven_distance_m` as compact label/value cells.

Implemented combined pipe/manhole table structure:

|  | 맨홀번호 | 맨홀종류 | 맨홀 내부재질 | 맨홀 뚜껑재질 | 맨홀 E.L(m) | 맨홀크기 | 맨홀깊이(m) | 맨홀 인버트 | 사다리모양 | 위도 | 경도 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 상류맨홀* | `{manhole_number}` | `{manhole_type}` | `{internal_material}` | `{cover_material}` | `{el_m}` | `{size}` | `{depth_m}` | `{invert}` | `{ladder_shape}` | `{latitude}` | `{longitude}` |
| 하류맨홀* | `{manhole_number}` | `{manhole_type}` | `{internal_material}` | `{cover_material}` | `{el_m}` | `{size}` | `{depth_m}` | `{invert}` | `{ladder_shape}` | `{latitude}` | `{longitude}` |
| 연장(m)* `{length_m}` |  |  | 총주행거리(m)* `{total_drive_distance_m}` |  |  | 완주여부 `{is_completed}` |  |  | 미주행거리(m) `{undriven_distance_m}` |  |  |

### 6.6 Actual Survey Information

Actual Survey Information belongs to one Report and is displayed as a separate table inside the pipe-information area.

The table has two directional rows and one freeform content row.

Directional row fields:

- `id`
- `report_id`
- `direction`
- `occurrence_point_m`
- `undriven_reason`
- `undriven_reason_detail`

Direction rows:

- `시작->끝`
- `끝->시작`

Allowed `undriven_reason` values:

- `없음`
- `맨홀뚜껑파손`
- `맨홀연결파손`
- `연결관돌출`
- `연결관접합부`
- `이음부`
- `침입수`
- `유출수`
- `부식`
- `관파속및크랙`
- `곡관로`
- `관침하`
- `관구배`
- `타관통과`
- `폐유`
- `모르타르`
- `토사퇴적`
- `개폐불가`
- `맨홀묻힘`
- `기타`

Freeform content row field:

- `survey_content`

Rules:

- The directional rows are fixed rows, not user-created repeating rows.
- The first column displays the fixed direction row label.
- The second column is displayed as `주행방향->맨홀번호` and shows the upstream/downstream manhole number direction.
- For `시작->끝`, the second column should show the upstream-to-downstream manhole numbers.
- For `끝->시작`, the second column should show the downstream-to-upstream manhole numbers.
- The third row keeps the first label cell as `조사내용` and merges the remaining cells for freeform content input.
- `survey_content` is independent of the directional row columns.
- Actual Survey Information can be saved while partially incomplete.

Implemented table structure:

|  | 주행방향->맨홀번호 | 발생지점(m) | 미주행사유 | 미주행사유설명 |
| --- | --- | --- | --- | --- |
| 시작->끝 | `{upstream_manhole_number}` -> `{downstream_manhole_number}` | `{occurrence_point_m}` | `{undriven_reason}` | `{undriven_reason_detail}` |
| 끝->시작 | `{downstream_manhole_number}` -> `{upstream_manhole_number}` | `{occurrence_point_m}` | `{undriven_reason}` | `{undriven_reason_detail}` |
| 조사내용 | `{survey_content}` |  |  |  |

### 6.7 Video

A Video belongs directly to one Report.

Fields:

- `id`
- `report_id`
- `file_path`
- `duration`
- `recorded_date`
- `scan_direction`
- `depth_roi_x`
- `depth_roi_y`
- `depth_roi_w`
- `depth_roi_h`

Rules:

- One Report has one Video.
- Registering a new video for a Report replaces the existing Report video after warning the user.
- When replacing the Report video, existing defects and capture images are preserved.
- Video files are referenced by path and are not duplicated by default.
- `scan_direction` allowed values: `순주행`, `역주행`.
- The old `정주행` value should no longer be used in new UI copy.

### 6.8 Defect

A Defect belongs directly to one Report and is captured from the Report video.

Fields:

- `id`
- `report_id`
- `video_id`
- `timestamp_ms`
- `image_path`
- `drive_direction`
- `distance_m`
- `item_category`
- `condition_item`
- `defect_item`
- `grade`
- `quadrant`
- `manhole_defect_depth_m`
- `memo`
- `created_at`

Allowed values:

- `drive_direction`: `순주행`, `역주행`
- `item_category`: `맨홀`, `관로`, `암거`
- `condition_item`: category-specific values defined in `docs/Defect_Taxonomy.md`
- `defect_item`: category-specific values defined in `docs/Defect_Taxonomy.md`
- `grade`: `대`, `중`, `소`
- `quadrant`:
  - `없음`
  - `상`
  - `상우하`
  - `우`
  - `우상`
  - `우상좌하`
  - `우하`
  - `좌`
  - `좌상`
  - `좌상우하`
  - `좌하`
  - `하`
  - `하우상`
  - `하좌상`
  - `전체`

Rules:

- Existing A/B/C grading must be replaced with `대/중/소`.
- Condition item selection is not a standard dropdown. It must use a grid-style picker matching the condition item reference UI.
- Manhole condition items differ from pipe/box-culvert condition items.
- Defect item selection must display item name, defect type (`구조`/`운영`), and grade scores (`대`/`중`/`소`) together.
- Empty grade-score cells in the taxonomy are invalid grade options and must not be displayed in the grade dropdown.
- When the selected defect item has exactly one valid grade, the grade is selected automatically and the grade dropdown shows only that grade.
- The defect list displays the computed score after `manhole_defect_depth_m`.
- Defect scores are computed from `item_category + defect_item + grade`; a separate persisted database score field is not required unless later requested.
- Defect capture remains report-video based: pause video, capture frame, enter defect fields, save.
- `distance_m` should be populated from OCR/depth recognition where available, but must be editable or manually enterable if recognition fails.
- `manhole_defect_depth_m` is distinct from pipe driving distance.
- Defect rows must store both `report_id` and `video_id`.

## 7. Filesystem Definition

The user selects a workspace root directory.

Recommended output structure:

```text
/{project_name}
   /{business_code}_{business_name}
      /{report_number}_{pipe_number}
         /captures
         {report_number}_InspectionReport.xlsx
         {report_number}_InspectionVisualReport.pdf
```

Rules:

- Video files are referenced by path and not duplicated by default.
- Capture frames are stored in `captures`.
- Capture filename format: `{video_filename}_{timestamp_ms}.png`.
- Path components must be sanitized for filesystem-invalid characters.

## 8. Functional Definition

### 8.1 Hierarchy Management

- Create, update, delete Projects.
- Create, update, delete Businesses under a selected Project.
- Create, update, delete Reports under a selected Business.
- Duplicate Projects, Businesses, and Reports from the right-panel management screens.
- Left navigation shows Project/Business/Report in three independent selection-only sections.
- Selecting non-Report nodes must clear or disable report-specific video/defect/export controls.

### 8.2 Report Detail Editing

Report detail page must allow editing:

- Report identity and survey fields
- Location/address fields
- Pipe type/category/specification fields
- Pipe information
- Upstream manhole information
- Downstream manhole information
- Actual survey information table

Report workspace UI layout:

- A selected Report opens as one vertically scrollable page without report/defect tabs.
- Top-right export actions: Excel report generation and PDF report generation.
- One combined `보고서 정보` table containing report basic information, location/drainage information, and pipe classification fields.
- One `관로 정보` section containing the pipe/manhole table and actual survey information table.
- Embedded video player, timeline, playback controls, and stop-segment controls.
- Compact defect registration controls.
- Defect list table.

Report workspace placement rules:

- The right panel contains the selected Report workspace as one scrollable page.
- Report management actions such as report add, duplicate, edit, delete, and list navigation are not shown inside an opened Report; they are handled from the Business-selected Report list screen.
- Report detail fields are automatically saved after editing; there is no manual `보고서 정보 저장` button inside the Report detail workspace.
- Excel/PDF export buttons are shown as plain top-right actions on the report workspace, not inside a separate Save/Export group box.
- PDF export can optionally merge another Report into the `AFTER` column of the visual report.
- When an AFTER Report is selected, the current Report defects are rendered in `BEFORE` and the selected Report defects are rendered in `AFTER`.
- AFTER Report merge is allowed only when all non-video and non-defect report inputs match the current Report, including Project/Business context, Report information, Pipe information, Manhole information, and Actual Survey information.
- `report_number` is excluded from AFTER Report merge equality checks so before/after reports can have different report numbers.
- If any merge-required information differs, PDF generation must be blocked and the user must be shown the differing fields.
- Project/Business summary is not shown inside the Report detail workspace.
- Report basic information, location/drainage information, and pipe classification fields are displayed as one table.
- Pipe/manhole information and actual survey information are displayed under one `관로 정보` section.
- Report detail tables are not wrapped by additional group boxes solely for decoration.
- The video player is embedded directly inside the report workspace and is not wrapped in an extra video/defect group box.
- The visible video metadata table is hidden in the report workspace.
- Defect registration places all fields except memo on the first row; memo is placed on the second row.
- Defect registration and defect list controls belong below the embedded video player.
- Defect analysis/display controls are placed at the top-right of the video control row.

Rules:

- Report can be automatically saved while incomplete.
- Required fields for export must be validated separately.
- Export buttons remain disabled until the required export field set is complete.

Initial required export field set:

- Report basic information: `report_number`, `pipe_number`, `survey_date`, `buried_years`
- Pipe Information: upstream manhole information, downstream manhole information, `length_m`, `total_drive_distance_m`

Manhole export-required rule:

- Upstream and downstream manhole information sections must exist for export.
- Individual manhole detail fields remain optional unless a later requirement marks them mandatory.

### 8.3 Video Playback

Controls:

- Play/Pause
- Frame-by-frame step
- +/-5 second jump
- Speed selection: 0.5x, 1x, 2x
- Current time display (`HH:MM:SS.ms`)
- Total duration display

Rules:

- Video playback is available only when a Report is selected.
- One Report can have only one active video.
- Timeline markers must show captured defects.
- Stop-segment markers may be shown separately when stop detection is used.

### 8.4 Defect Registration

Workflow:

1. Select a Report.
2. Load or register the Report video.
3. Pause video at the target frame.
4. Capture current frame.
5. Enter defect attributes.
6. Save defect.

Save operation must:

- Persist PNG frame to disk.
- Persist defect record with timestamp.
- Store report/video ownership.
- Refresh defect list.
- Add timeline marker.
- Recompute grade summary in real time.

### 8.5 Defect Management

- List all defects for selected Report.
- Show related timestamp, distance, grade, condition item, defect item, quadrant, and image path.
- Jump playback to defect timestamp.
- Edit defect fields.
- Delete defect and remove corresponding image file.

### 8.6 Real-Time Grade Summary

For selected Report, compute on demand:

- `대` count
- `중` count
- `소` count
- Total count

Rule:

- Do not cache summary counters in DB.

### 8.7 Excel Reporting

Trigger:

- Generate Excel report for selected Report.

Output filename:

- `{report_number}_InspectionReport.xlsx`

Behavior:

- Ask overwrite confirmation if target file exists.
- Export button must be disabled until required export fields are complete.

Workbook must reflect:

- Project fields
- Business fields
- Report fields
- Combined pipe/manhole information table
- Actual survey information table
- Detailed defect list
- Defect image thumbnails
- Grade color coding for `대/중/소`

Excel workbook sheet layout:

1. `기본정보`
   - Project/Business summary table
   - Report basic information table
   - Location/drainage information table
   - Pipe classification table
2. `관로조사정보`
   - Combined pipe/manhole information table
   - Actual survey information table
3. `결함목록`
   - Detailed defect list table
   - Defect image thumbnails
   - Grade color coding for `대/중/소`

The video information table is part of the desktop report workspace, but it does not need to be exported unless explicitly requested later.

### 8.8 PDF Visual Reporting

Trigger:

- Generate PDF visual report for selected Report.

Output filename:

- `{report_number}_InspectionVisualReport.pdf`

PDF defect display scope:

- `condition_item`
- `defect_item`
- `grade`

Rules:

- PDF does not need to display every newly added Report/Business/Pipe/Manhole field.
- PDF still uses the pipe visual layout.
- Defect placement should use `distance_m` where available.
- Existing PDF defect captions must be replaced with the new limited caption scope: `condition_item`, `defect_item`, and `grade`.

## 9. Keyboard Shortcuts

- Play/Pause: `Space`
- Capture: `C`
- Grade large: `3`
- Grade medium: `2`
- Grade small: `1`
- Save defect: `Enter`
- Delete selected defect: `Delete`
- Frame step: `Left`, `Right`
- +/-5 seconds jump: `Shift+Left`, `Shift+Right`

Grade shortcut mapping:

- `3` maps to `대`
- `2` maps to `중`
- `1` maps to `소`

## 10. Integrity, Logging, and Reliability

- `PRAGMA foreign_keys=ON` required.
- Deleting a Project cascades to Businesses, Reports, Videos, Pipe Information, Manholes, Actual Survey Information, and Defects.
- Deleting a Business cascades to Reports and their child records.
- Deleting a Report cascades to its Video, Pipe Information, Manholes, Actual Survey Information, and Defects.
- Defect deletion must remove image file.
- DB write operations use transactions.
- App crash should not corrupt DB journal/state.
- `error.log` stores critical exceptions, DB failures, and path failures.

## 11. Implementation Constraints

- Do not implement old DB migration.
- When an old workspace DB exists, old `zones`/`pipes`-based data does not need to appear in the new Project/Business/Report UI.
- Replace old `Zone` concepts with `Business`.
- Replace old `Pipe` ownership concepts with `Report`.
- Move video and defect ownership from `Pipe` to `Report`.
- Preserve report-video inspection workflow.
- Update UI labels from `정주행/역주행` to `순주행/역주행`.
- Before code changes start, implementation permission must be confirmed by the user.

## 12. Acceptance Criteria

- Left navigation clearly presents Project/Business/Report as distinct boxed sections.
- A Project can contain multiple Businesses.
- A Business can contain multiple Reports.
- A Report can contain exactly one active Video.
- Defects are registered and listed per Report.
- Incomplete Report details can be saved.
- Actual Survey Information is edited and exported as a fixed table with two directional rows and one merged content row.
- Export actions are disabled until required export fields are complete.
- Excel output reflects Project, Business, Report, Pipe Information, Manhole, Actual Survey Information, and Defect fields.
- PDF output includes at least condition item, defect item, and grade for defects.
- Existing automated tests are updated or replaced to cover the new model.
