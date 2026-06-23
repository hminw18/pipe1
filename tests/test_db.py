import sqlite3
import shutil
from pathlib import Path

import pytest

from sewerpipe_inspector.db import ACTUAL_SURVEY_FIELDS, Database, MANHOLE_FIELDS, REPORT_FIELDS


def _make_report(db: Database) -> int:
    project_id = db.create_project("P1")
    business_id = db.create_business(
        project_id, "B001", "사업1", "Client", "2026-01-01", "2026-12-31"
    )
    return db.create_report(business_id, "R001", "PIPE-001")


def test_foreign_key_cascade_on_report_delete(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")
    db.create_defect(
        report_id,
        video_id,
        1500,
        str(tmp_path / "cap.png"),
        "순주행",
        1.2,
        "관로",
        "구조특징",
        "균열(길이)",
        "대",
        "상",
        None,
        None,
    )

    assert db.get_video(report_id) is not None
    assert len(db.list_defects(report_id)) == 1

    db.delete_report(report_id)

    assert db.get_video(report_id) is None
    assert db.list_defects(report_id) == []


def test_grade_counts_dynamic(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")

    db.create_defect(
        report_id, video_id, 1000, str(tmp_path / "a.png"), "순주행", 1.0,
        "관로", "구조특징", "균열(길이)", "대", "상", None, None
    )
    db.create_defect(
        report_id, video_id, 2000, str(tmp_path / "b.png"), "역주행", 2.0,
        "관로", "이상없음", "침입수", "소", "하", None, None
    )

    counts = db.get_grade_counts(report_id)
    assert counts.large == 1
    assert counts.medium == 0
    assert counts.small == 1
    assert counts.total == 2


def test_condition_item_defect_can_have_no_grade(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")

    defect_id = db.create_defect(
        report_id,
        video_id,
        1000,
        str(tmp_path / "a.png"),
        "순주행",
        None,
        "관로",
        "조사완료(순방향)",
        None,
        None,
        None,
        None,
        None,
    )

    defect = db.get_defect(defect_id)
    assert defect["condition_item"] == "조사완료(순방향)"
    assert defect["defect_item"] is None
    assert defect["grade"] is None


def test_old_grade_not_null_schema_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE inspection_projects (
                id INTEGER PRIMARY KEY,
                project_name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                business_code TEXT NOT NULL,
                business_name TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES inspection_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE reports (
                id INTEGER PRIMARY KEY,
                business_id INTEGER NOT NULL,
                report_number TEXT NOT NULL,
                pipe_number TEXT NOT NULL,
                FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
            );
            CREATE TABLE report_videos (
                id INTEGER PRIMARY KEY,
                report_id INTEGER NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                scan_direction TEXT NOT NULL DEFAULT '순주행'
                    CHECK(scan_direction IN ('순주행', '역주행')),
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
            );
            CREATE TABLE report_defects (
                id INTEGER PRIMARY KEY,
                report_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                drive_direction TEXT NOT NULL CHECK(drive_direction IN ('순주행', '역주행')),
                distance_m REAL,
                item_category TEXT,
                condition_item TEXT,
                defect_item TEXT,
                grade TEXT NOT NULL CHECK(grade IN ('대', '중', '소')),
                quadrant TEXT,
                manhole_defect_depth_m REAL,
                memo TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
                FOREIGN KEY(video_id) REFERENCES report_videos(id)
            );
            INSERT INTO inspection_projects(id, project_name) VALUES (1, 'P1');
            INSERT INTO businesses(id, project_id, business_code, business_name)
                VALUES (1, 1, 'B001', '사업1');
            INSERT INTO reports(id, business_id, report_number, pipe_number)
                VALUES (1, 1, 'R001', 'PIPE-001');
            INSERT INTO report_videos(id, report_id, file_path, scan_direction)
                VALUES (1, 1, 'a.mp4', '순주행');
            INSERT INTO report_defects(
                id, report_id, video_id, timestamp_ms, image_path,
                drive_direction, item_category, condition_item, defect_item, grade
            )
            VALUES (
                1, 1, 1, 1000, 'cap.png',
                '순주행', '관로', '구조특징', '균열(길이)', '대'
            );
            """
        )

    db = Database(db_path)
    grade_column = next(
        row for row in db.fetchall("PRAGMA table_info(report_defects)")
        if row["name"] == "grade"
    )
    assert grade_column["notnull"] == 0
    migrated = db.get_defect(1)
    assert migrated["condition_item"] is None
    assert migrated["defect_item"] == "균열(길이)"
    assert migrated["grade"] == "대"


def test_video_replacement_preserves_defects(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)
    video_id = db.upsert_video(report_id, str(tmp_path / "old.mp4"), 10.0, None, "순주행")
    defect_id = db.create_defect(
        report_id, video_id, 1000, str(tmp_path / "a.png"), "순주행", 1.0,
        "관로", "구조특징", "균열(길이)", "중", "상", None, None
    )

    new_video_id = db.upsert_video(
        report_id, str(tmp_path / "new.mp4"), 20.0, None, "역주행"
    )

    assert new_video_id == video_id
    assert db.get_video(report_id)["file_path"].endswith("new.mp4")
    assert db.get_defect(defect_id) is not None
    assert len(db.list_defects(report_id)) == 1


def test_duplicate_report_copies_inputs_without_video_or_defects(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)
    report_payload = {field: None for field in REPORT_FIELDS}
    report_payload.update(
        {
            "report_number": "R001",
            "pipe_number": "PIPE-001",
            "survey_date": "2026-06-14",
            "buried_years": "12",
            "inspector": "Inspector",
        }
    )
    db.update_report(report_id, report_payload)
    db.update_manhole(
        report_id,
        "upstream",
        {field: ("MH-U" if field == "manhole_number" else None) for field in MANHOLE_FIELDS},
    )
    db.update_pipe_information(report_id, 10.0, 7.0)
    actual_payload = {field: None for field in ACTUAL_SURVEY_FIELDS}
    actual_payload["start_undriven_reason"] = "곡관로"
    actual_payload["end_undriven_reason"] = "없음"
    actual_payload["survey_content"] = "조사내용"
    db.update_actual_survey(report_id, actual_payload)
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")
    db.create_defect(
        report_id, video_id, 1000, str(tmp_path / "a.png"), "순주행", 1.0,
        "관로", "구조특징", "균열(길이)", "중", "상", None, None
    )

    copied_id = db.duplicate_report(report_id)

    copied = db.get_report(copied_id)
    assert copied["report_number"] == "R001 복사본"
    assert copied["pipe_number"] == "PIPE-001"
    assert copied["survey_date"] == "2026-06-14"
    assert db.get_manhole(copied_id, "upstream")["manhole_number"] == "MH-U"
    copied_pipe = db.get_pipe_information(copied_id)
    assert copied_pipe["length_m"] == 10.0
    assert copied_pipe["total_drive_distance_m"] == 7.0
    assert copied_pipe["undriven_distance_m"] == 3.0
    assert db.get_actual_survey(copied_id)["survey_content"] == "조사내용"
    assert db.get_video(copied_id) is None
    assert db.list_defects(copied_id) == []


def test_business_code_must_be_unique_within_project(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    project_id = db.create_project("P1")
    other_project_id = db.create_project("P2")
    business_id = db.create_business(project_id, "B001", "사업1", None, None, None)

    with pytest.raises(ValueError, match="같은 프로젝트"):
        db.create_business(project_id, "B001", "사업2", None, None, None)

    db.create_business(other_project_id, "B001", "사업3", None, None, None)
    other_business_id = db.create_business(project_id, "B002", "사업4", None, None, None)

    with pytest.raises(ValueError, match="같은 프로젝트"):
        db.update_business(other_business_id, "B001", "사업4", None, None, None)

    db.update_business(business_id, "B001", "사업1 수정", None, None, None)


def test_report_number_must_be_unique_within_business(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    project_id = db.create_project("P1")
    business_id = db.create_business(project_id, "B001", "사업1", None, None, None)
    other_business_id = db.create_business(project_id, "B002", "사업2", None, None, None)
    report_id = db.create_report(business_id, "R001", "PIPE-001")

    with pytest.raises(ValueError, match="같은 사업"):
        db.create_report(business_id, "R001", "PIPE-002")

    db.create_report(other_business_id, "R001", "PIPE-003")
    other_report_id = db.create_report(business_id, "R002", "PIPE-004")
    payload = {field: None for field in REPORT_FIELDS}
    payload.update({"report_number": "R001", "pipe_number": "PIPE-004"})

    with pytest.raises(ValueError, match="같은 사업"):
        db.update_report(other_report_id, payload)

    payload["report_number"] = "R001"
    payload["pipe_number"] = "PIPE-001 수정"
    db.update_report(report_id, payload)


def test_duplicate_report_number_is_made_unique(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)

    first_copy_id = db.duplicate_report(report_id)
    second_copy_id = db.duplicate_report(report_id)

    assert db.get_report(first_copy_id)["report_number"] == "R001 복사본"
    assert db.get_report(second_copy_id)["report_number"] == "R001 복사본 2"


def test_existing_duplicate_business_and_report_numbers_are_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "old_duplicates.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE inspection_projects (
                id INTEGER PRIMARY KEY,
                project_name TEXT NOT NULL
            );
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                business_code TEXT NOT NULL,
                business_name TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES inspection_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE reports (
                id INTEGER PRIMARY KEY,
                business_id INTEGER NOT NULL,
                report_number TEXT NOT NULL,
                pipe_number TEXT NOT NULL,
                FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
            );
            INSERT INTO inspection_projects(id, project_name) VALUES (1, 'P1');
            INSERT INTO businesses(id, project_id, business_code, business_name)
                VALUES (1, 1, 'B001', '사업1'), (2, 1, 'B001', '사업2');
            INSERT INTO reports(id, business_id, report_number, pipe_number)
                VALUES (1, 1, 'R001', 'PIPE-001'), (2, 1, 'R001', 'PIPE-002');
            """
        )

    db = Database(db_path)

    business_codes = [
        row["business_code"]
        for row in db.fetchall("SELECT * FROM businesses ORDER BY id")
    ]
    report_numbers = [
        row["report_number"]
        for row in db.fetchall("SELECT * FROM reports ORDER BY id")
    ]
    assert business_codes == ["B001", "B001 2"]
    assert report_numbers == ["R001", "R001 2"]
    with pytest.raises(ValueError, match="같은 프로젝트"):
        db.create_business(1, "B001", "사업3", None, None, None)
    with pytest.raises(ValueError, match="같은 사업"):
        db.create_report(1, "R001", "PIPE-003")


def test_report_defect_performance_indexes_exist(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")

    indexes = {
        row["name"]
        for row in db.fetchall("PRAGMA index_list(report_defects)")
    }
    assert "idx_report_defects_report_time" in indexes
    assert "idx_report_defects_report_grade" in indexes
    assert "idx_report_defects_video" in indexes

    plan = db.fetchall(
        """
        EXPLAIN QUERY PLAN
        SELECT report_defects.*, report_videos.file_path AS video_file_path
        FROM report_defects
        JOIN report_videos ON report_videos.id = report_defects.video_id
        WHERE report_defects.report_id = ?
        ORDER BY report_defects.timestamp_ms ASC, report_defects.id ASC
        """,
        (1,),
    )
    assert any(
        "idx_report_defects_report_time" in row["detail"]
        for row in plan
    )


def test_database_recreates_missing_workspace_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace" / "sewerpipe_inspector.db"
    db = Database(db_path)
    db.create_project("P1")

    shutil.rmtree(db_path.parent)

    assert db.list_projects() == []
    assert db_path.exists()
    indexes = {
        row["name"]
        for row in db.fetchall("PRAGMA index_list(report_defects)")
    }
    assert "idx_report_defects_report_time" in indexes


def test_pipe_completion_is_derived(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    report_id = _make_report(db)

    db.update_pipe_information(report_id, 12.5, 12.5)
    pipe_info = db.get_pipe_information(report_id)
    assert pipe_info["is_completed"] == 1
    assert pipe_info["undriven_distance_m"] == 0

    db.update_pipe_information(report_id, 12.5, 10.0)
    pipe_info = db.get_pipe_information(report_id)
    assert pipe_info["is_completed"] == 0
    assert pipe_info["undriven_distance_m"] == 2.5
