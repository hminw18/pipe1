from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


REPORT_FIELDS = [
    "report_number",
    "pipe_number",
    "survey_purpose",
    "survey_date",
    "buried_years",
    "inspector",
    "contractor",
    "treatment_area",
    "drainage_area",
    "drainage_district",
    "drain_type",
    "drain_system",
    "province",
    "city_county",
    "town",
    "village",
    "lot_number",
    "road_address",
    "pipe_type",
    "category",
    "specification",
]

MANHOLE_FIELDS = [
    "manhole_number",
    "manhole_type",
    "internal_material",
    "cover_material",
    "el_m",
    "size",
    "depth_m",
    "invert",
    "ladder_shape",
    "latitude",
    "longitude",
]

ACTUAL_SURVEY_FIELDS = [
    "start_occurrence_point_m",
    "start_undriven_reason",
    "start_undriven_reason_detail",
    "end_occurrence_point_m",
    "end_undriven_reason",
    "end_undriven_reason_detail",
    "survey_content",
]

DEFECT_FIELDS = [
    "drive_direction",
    "distance_m",
    "item_category",
    "condition_item",
    "defect_item",
    "grade",
    "quadrant",
    "manhole_defect_depth_m",
    "memo",
]

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS inspection_projects (
    id INTEGER PRIMARY KEY,
    project_name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    business_code TEXT NOT NULL,
    business_name TEXT NOT NULL,
    client TEXT,
    business_start_date TEXT,
    business_end_date TEXT,
    FOREIGN KEY(project_id) REFERENCES inspection_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    report_number TEXT NOT NULL,
    pipe_number TEXT NOT NULL,
    survey_purpose TEXT,
    survey_date TEXT,
    buried_years TEXT,
    inspector TEXT,
    contractor TEXT,
    treatment_area TEXT,
    drainage_area TEXT,
    drainage_district TEXT,
    drain_type TEXT,
    drain_system TEXT,
    province TEXT,
    city_county TEXT,
    town TEXT,
    village TEXT,
    lot_number TEXT,
    road_address TEXT,
    pipe_type TEXT,
    category TEXT,
    specification TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manholes (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('upstream', 'downstream')),
    manhole_number TEXT,
    manhole_type TEXT,
    internal_material TEXT,
    cover_material TEXT,
    el_m TEXT,
    size TEXT,
    depth_m TEXT,
    invert TEXT,
    ladder_shape TEXT,
    latitude TEXT,
    longitude TEXT,
    UNIQUE(report_id, role),
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipe_information (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL UNIQUE,
    upstream_manhole_id INTEGER,
    downstream_manhole_id INTEGER,
    length_m REAL,
    total_drive_distance_m REAL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    undriven_distance_m REAL,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY(upstream_manhole_id) REFERENCES manholes(id) ON DELETE SET NULL,
    FOREIGN KEY(downstream_manhole_id) REFERENCES manholes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS actual_survey_information (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL UNIQUE,
    start_occurrence_point_m REAL,
    start_undriven_reason TEXT DEFAULT '없음',
    start_undriven_reason_detail TEXT,
    end_occurrence_point_m REAL,
    end_undriven_reason TEXT DEFAULT '없음',
    end_undriven_reason_detail TEXT,
    survey_content TEXT,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS report_videos (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    duration REAL,
    recorded_date TEXT,
    scan_direction TEXT NOT NULL DEFAULT '순주행' CHECK(scan_direction IN ('순주행', '역주행')),
    depth_roi_x INTEGER,
    depth_roi_y INTEGER,
    depth_roi_w INTEGER,
    depth_roi_h INTEGER,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS report_defects (
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
    grade TEXT CHECK(grade IN ('대', '중', '소')),
    quadrant TEXT,
    manhole_defect_depth_m REAL,
    memo TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY(video_id) REFERENCES report_videos(id)
);
"""


@dataclass
class GradeCounts:
    large: int
    medium: int
    small: int
    total: int


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_schema()

    def _open_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _connect(self) -> sqlite3.Connection:
        needs_schema = not self.db_path.exists()
        conn = self._open_connection()
        if needs_schema:
            self._initialize_schema(conn)
            self._run_migrations_on_connection(conn)
            conn.commit()
        return conn

    def _init_schema(self) -> None:
        with self._open_connection() as conn:
            self._initialize_schema(conn)
        self._run_migrations()

    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_SQL)

    def _run_migrations(self) -> None:
        # New-table migrations live here. Old zones/pipes tables are intentionally
        # ignored because existing data migration is out of scope.
        with self.transaction() as conn:
            self._run_migrations_on_connection(conn)

    def _run_migrations_on_connection(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "report_defects",
            {
                "drive_direction": "TEXT NOT NULL DEFAULT '순주행'",
                "distance_m": "REAL",
                "item_category": "TEXT",
                "condition_item": "TEXT",
                "defect_item": "TEXT",
                "quadrant": "TEXT",
                "manhole_defect_depth_m": "REAL",
            },
        )
        self._ensure_nullable_defect_grade(conn)
        self._normalize_defect_item_columns(conn)
        self._ensure_unique_entity_numbers(conn)
        self._ensure_performance_indexes(conn)

    def _ensure_unique_entity_numbers(self, conn: sqlite3.Connection) -> None:
        self._deduplicate_scoped_text(
            conn, "businesses", "project_id", "business_code"
        )
        self._deduplicate_scoped_text(
            conn, "reports", "business_id", "report_number"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                ux_businesses_project_business_code
            ON businesses(project_id, business_code)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                ux_reports_business_report_number
            ON reports(business_id, report_number)
            """
        )

    def _ensure_performance_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_report_defects_report_time
            ON report_defects(report_id, timestamp_ms, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_report_defects_report_grade
            ON report_defects(report_id, grade)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_report_defects_video
            ON report_defects(video_id)
            """
        )

    def _deduplicate_scoped_text(
        self,
        conn: sqlite3.Connection,
        table: str,
        scope_column: str,
        value_column: str,
    ) -> None:
        rows = conn.execute(
            f"""
            SELECT id, {scope_column}, {value_column}
            FROM {table}
            ORDER BY {scope_column}, id
            """
        ).fetchall()
        seen: dict[object, set[str]] = {}
        for row in rows:
            scope_value = row[scope_column]
            value = str(row[value_column])
            scoped_values = seen.setdefault(scope_value, set())
            if value not in scoped_values:
                scoped_values.add(value)
                continue

            unique_value = self._next_unique_text_in_scope(
                conn,
                table,
                value_column,
                value,
                f"{scope_column} = ?",
                (scope_value,),
                exclude_id=int(row["id"]),
                reserved=scoped_values,
            )
            conn.execute(
                f"UPDATE {table} SET {value_column} = ? WHERE id = ?",
                (unique_value, int(row["id"])),
            )
            scoped_values.add(unique_value)

    def _ensure_nullable_defect_grade(self, conn: sqlite3.Connection) -> None:
        table_info = conn.execute("PRAGMA table_info(report_defects)").fetchall()
        grade_info = next((row for row in table_info if row["name"] == "grade"), None)
        if grade_info is None or int(grade_info["notnull"]) == 0:
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            CREATE TABLE report_defects_new (
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
                grade TEXT CHECK(grade IN ('대', '중', '소')),
                quadrant TEXT,
                manhole_defect_depth_m REAL,
                memo TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
                FOREIGN KEY(video_id) REFERENCES report_videos(id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO report_defects_new(
                id, report_id, video_id, timestamp_ms, image_path,
                drive_direction, distance_m, item_category, condition_item,
                defect_item, grade, quadrant, manhole_defect_depth_m, memo,
                created_at
            )
            SELECT
                id,
                report_id,
                video_id,
                timestamp_ms,
                image_path,
                drive_direction,
                distance_m,
                item_category,
                CASE
                    WHEN NULLIF(TRIM(COALESCE(defect_item, '')), '') IS NOT NULL
                    THEN NULL
                    ELSE NULLIF(TRIM(COALESCE(condition_item, '')), '')
                END,
                NULLIF(TRIM(COALESCE(defect_item, '')), ''),
                CASE
                    WHEN NULLIF(TRIM(COALESCE(defect_item, '')), '') IS NOT NULL
                         AND grade IN ('대', '중', '소')
                    THEN grade
                    ELSE NULL
                END,
                quadrant,
                manhole_defect_depth_m,
                memo,
                created_at
            FROM report_defects
            """
        )
        conn.execute("DROP TABLE report_defects")
        conn.execute("ALTER TABLE report_defects_new RENAME TO report_defects")
        conn.execute("PRAGMA foreign_keys = ON")

    def _normalize_defect_item_columns(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE report_defects
            SET condition_item = NULL
            WHERE NULLIF(TRIM(COALESCE(defect_item, '')), '') IS NOT NULL
            """
        )
        conn.execute(
            """
            UPDATE report_defects
            SET
                condition_item = NULLIF(TRIM(COALESCE(condition_item, '')), ''),
                defect_item = NULLIF(TRIM(COALESCE(defect_item, '')), ''),
                grade = CASE
                    WHEN NULLIF(TRIM(COALESCE(defect_item, '')), '') IS NOT NULL
                         AND grade IN ('대', '중', '소')
                    THEN grade
                    ELSE NULL
                END
            """
        )

    def _ensure_columns(
        self, conn: sqlite3.Connection, table_name: str, columns: dict[str, str]
    ) -> None:
        existing = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fetchall(
        self, query: str, params: tuple[object, ...] = ()
    ) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(query, params).fetchall())

    def fetchone(
        self, query: str, params: tuple[object, ...] = ()
    ) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(query, params).fetchone()

    def execute(self, query: str, params: tuple[object, ...] = ()) -> int:
        with self.transaction() as conn:
            cur = conn.execute(query, params)
            return int(cur.lastrowid or 0)

    def create_project(self, project_name: str) -> int:
        return self.execute(
            "INSERT INTO inspection_projects(project_name) VALUES (?)",
            (project_name,),
        )

    def update_project(self, project_id: int, project_name: str) -> None:
        self.execute(
            "UPDATE inspection_projects SET project_name = ? WHERE id = ?",
            (project_name, project_id),
        )

    def get_project(self, project_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM inspection_projects WHERE id = ?", (project_id,)
        )

    def delete_project(self, project_id: int) -> None:
        self.execute("DELETE FROM inspection_projects WHERE id = ?", (project_id,))

    def list_projects(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM inspection_projects ORDER BY id DESC")

    def list_projects_with_counts(self) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT
                inspection_projects.*,
                COUNT(DISTINCT businesses.id) AS business_count,
                COUNT(DISTINCT reports.id) AS report_count
            FROM inspection_projects
            LEFT JOIN businesses ON businesses.project_id = inspection_projects.id
            LEFT JOIN reports ON reports.business_id = businesses.id
            GROUP BY inspection_projects.id
            ORDER BY inspection_projects.id DESC
            """
        )

    def create_business(
        self,
        project_id: int,
        business_code: str,
        business_name: str,
        client: str | None,
        business_start_date: str | None,
        business_end_date: str | None,
    ) -> int:
        with self.transaction() as conn:
            self._require_unique_business_code(conn, project_id, business_code)
            cur = conn.execute(
                """
                INSERT INTO businesses(
                    project_id, business_code, business_name, client,
                    business_start_date, business_end_date
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    business_code,
                    business_name,
                    client,
                    business_start_date,
                    business_end_date,
                ),
            )
            return int(cur.lastrowid)

    def update_business(
        self,
        business_id: int,
        business_code: str,
        business_name: str,
        client: str | None,
        business_start_date: str | None,
        business_end_date: str | None,
    ) -> None:
        with self.transaction() as conn:
            business = conn.execute(
                "SELECT project_id FROM businesses WHERE id = ?", (business_id,)
            ).fetchone()
            if business is None:
                raise ValueError("사업을 찾을 수 없습니다")
            self._require_unique_business_code(
                conn, int(business["project_id"]), business_code, business_id
            )
            conn.execute(
                """
                UPDATE businesses
                SET business_code = ?, business_name = ?, client = ?,
                    business_start_date = ?, business_end_date = ?
                WHERE id = ?
                """,
                (
                    business_code,
                    business_name,
                    client,
                    business_start_date,
                    business_end_date,
                    business_id,
                ),
            )

    def delete_business(self, business_id: int) -> None:
        self.execute("DELETE FROM businesses WHERE id = ?", (business_id,))

    def get_business(self, business_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM businesses WHERE id = ?", (business_id,))

    def list_businesses(self, project_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM businesses WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        )

    def list_businesses_with_counts(self, project_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT
                businesses.*,
                COUNT(reports.id) AS report_count
            FROM businesses
            LEFT JOIN reports ON reports.business_id = businesses.id
            WHERE businesses.project_id = ?
            GROUP BY businesses.id
            ORDER BY businesses.id DESC
            """,
            (project_id,),
        )

    def create_report(
        self,
        business_id: int,
        report_number: str,
        pipe_number: str,
    ) -> int:
        with self.transaction() as conn:
            self._require_unique_report_number(conn, business_id, report_number)
            cur = conn.execute(
                """
                INSERT INTO reports(business_id, report_number, pipe_number)
                VALUES (?, ?, ?)
                """,
                (business_id, report_number, pipe_number),
            )
            report_id = int(cur.lastrowid)
            self._ensure_report_children(conn, report_id)
            return report_id

    def _ensure_report_children(self, conn: sqlite3.Connection, report_id: int) -> None:
        for role in ("upstream", "downstream"):
            conn.execute(
                """
                INSERT OR IGNORE INTO manholes(report_id, role)
                VALUES (?, ?)
                """,
                (report_id, role),
            )
        upstream = conn.execute(
            "SELECT id FROM manholes WHERE report_id = ? AND role = 'upstream'",
            (report_id,),
        ).fetchone()
        downstream = conn.execute(
            "SELECT id FROM manholes WHERE report_id = ? AND role = 'downstream'",
            (report_id,),
        ).fetchone()
        if upstream is None or downstream is None:
            raise RuntimeError("Failed to initialize report manholes")
        conn.execute(
            """
            INSERT OR IGNORE INTO pipe_information(
                report_id, upstream_manhole_id, downstream_manhole_id
            )
            VALUES (?, ?, ?)
            """,
            (report_id, upstream["id"], downstream["id"]),
        )
        conn.execute(
            """
            UPDATE pipe_information
            SET upstream_manhole_id = ?, downstream_manhole_id = ?
            WHERE report_id = ?
            """,
            (upstream["id"], downstream["id"], report_id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO actual_survey_information(
                report_id, start_undriven_reason, end_undriven_reason
            )
            VALUES (?, '없음', '없음')
            """,
            (report_id,),
        )

    def ensure_report_children(self, report_id: int) -> None:
        with self.transaction() as conn:
            self._ensure_report_children(conn, report_id)

    def update_report(self, report_id: int, payload: dict[str, object]) -> None:
        with self.transaction() as conn:
            report = conn.execute(
                "SELECT business_id FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            if report is None:
                raise ValueError("보고서를 찾을 수 없습니다")
            report_number = payload.get("report_number")
            self._require_unique_report_number(
                conn,
                int(report["business_id"]),
                "" if report_number is None else str(report_number),
                report_id,
            )
            values = [payload.get(field) for field in REPORT_FIELDS]
            assignments = ", ".join(f"{field} = ?" for field in REPORT_FIELDS)
            conn.execute(
                f"UPDATE reports SET {assignments} WHERE id = ?",
                tuple(values + [report_id]),
            )

    def delete_report(self, report_id: int) -> None:
        self.execute("DELETE FROM reports WHERE id = ?", (report_id,))

    def get_report(self, report_id: int) -> Optional[sqlite3.Row]:
        self.ensure_report_children(report_id)
        return self.fetchone("SELECT * FROM reports WHERE id = ?", (report_id,))

    def list_reports(self, business_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM reports WHERE business_id = ? ORDER BY id DESC",
            (business_id,),
        )

    def list_reports_with_counts(self, business_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT
                reports.*,
                report_videos.id AS video_id,
                pipe_information.length_m,
                pipe_information.total_drive_distance_m,
                pipe_information.is_completed,
                COUNT(report_defects.id) AS defect_count
            FROM reports
            LEFT JOIN report_videos ON report_videos.report_id = reports.id
            LEFT JOIN pipe_information ON pipe_information.report_id = reports.id
            LEFT JOIN report_defects ON report_defects.report_id = reports.id
            WHERE reports.business_id = ?
            GROUP BY reports.id
            ORDER BY reports.id DESC
            """,
            (business_id,),
        )

    def list_report_contexts(self) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT
                reports.*,
                businesses.id AS business_id,
                businesses.business_code,
                businesses.business_name,
                inspection_projects.id AS project_id,
                inspection_projects.project_name
            FROM reports
            JOIN businesses ON businesses.id = reports.business_id
            JOIN inspection_projects ON inspection_projects.id = businesses.project_id
            ORDER BY inspection_projects.project_name ASC,
                     businesses.business_code ASC,
                     businesses.business_name ASC,
                     reports.report_number ASC,
                     reports.pipe_number ASC,
                     reports.id ASC
            """
        )

    def get_report_context(self, report_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone(
            """
            SELECT
                reports.*,
                businesses.id AS business_id,
                businesses.business_code,
                businesses.business_name,
                businesses.client,
                businesses.business_start_date,
                businesses.business_end_date,
                inspection_projects.id AS project_id,
                inspection_projects.project_name,
                inspection_projects.created_at AS project_created_at
            FROM reports
            JOIN businesses ON businesses.id = reports.business_id
            JOIN inspection_projects ON inspection_projects.id = businesses.project_id
            WHERE reports.id = ?
            """,
            (report_id,),
        )

    @staticmethod
    def _copy_text(value: object, fallback: str) -> str:
        base = str(value).strip() if value not in (None, "") else fallback
        return f"{base} 복사본"

    def _scoped_text_exists(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        value: str,
        scope_clause: str,
        scope_params: tuple[object, ...],
        exclude_id: int | None = None,
    ) -> bool:
        sql = f"SELECT 1 FROM {table} WHERE {scope_clause} AND {column} = ?"
        params: list[object] = [*scope_params, value]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        sql += " LIMIT 1"
        return conn.execute(sql, tuple(params)).fetchone() is not None

    def _next_unique_text_in_scope(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        base: str,
        scope_clause: str,
        scope_params: tuple[object, ...],
        exclude_id: int | None = None,
        reserved: set[str] | None = None,
    ) -> str:
        candidate = base
        suffix = 2
        reserved = reserved or set()
        while (
            candidate in reserved
            or self._scoped_text_exists(
                conn, table, column, candidate, scope_clause, scope_params, exclude_id
            )
        ):
            candidate = f"{base} {suffix}"
            suffix += 1
        return candidate

    def _business_code_exists(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        business_code: str,
        exclude_id: int | None = None,
    ) -> bool:
        return self._scoped_text_exists(
            conn,
            "businesses",
            "business_code",
            business_code,
            "project_id = ?",
            (project_id,),
            exclude_id,
        )

    def _report_number_exists(
        self,
        conn: sqlite3.Connection,
        business_id: int,
        report_number: str,
        exclude_id: int | None = None,
    ) -> bool:
        return self._scoped_text_exists(
            conn,
            "reports",
            "report_number",
            report_number,
            "business_id = ?",
            (business_id,),
            exclude_id,
        )

    def _require_unique_business_code(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        business_code: str,
        exclude_id: int | None = None,
    ) -> None:
        if not business_code:
            raise ValueError("사업번호를 입력하세요")
        if self._business_code_exists(conn, project_id, business_code, exclude_id):
            raise ValueError("같은 프로젝트 안에 같은 사업번호가 이미 있습니다")

    def _require_unique_report_number(
        self,
        conn: sqlite3.Connection,
        business_id: int,
        report_number: str,
        exclude_id: int | None = None,
    ) -> None:
        if not report_number:
            raise ValueError("보고서번호를 입력하세요")
        if self._report_number_exists(conn, business_id, report_number, exclude_id):
            raise ValueError("같은 사업 안에 같은 보고서번호가 이미 있습니다")

    def _unique_business_copy_code(
        self, conn: sqlite3.Connection, project_id: int, base: str
    ) -> str:
        return self._next_unique_text_in_scope(
            conn,
            "businesses",
            "business_code",
            base,
            "project_id = ?",
            (project_id,),
        )

    def _unique_report_copy_number(
        self, conn: sqlite3.Connection, business_id: int, base: str
    ) -> str:
        return self._next_unique_text_in_scope(
            conn,
            "reports",
            "report_number",
            base,
            "business_id = ?",
            (business_id,),
        )

    def duplicate_project(self, project_id: int) -> int:
        with self.transaction() as conn:
            project = conn.execute(
                "SELECT * FROM inspection_projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("프로젝트를 찾을 수 없습니다")
            cur = conn.execute(
                "INSERT INTO inspection_projects(project_name) VALUES (?)",
                (self._copy_text(project["project_name"], "프로젝트"),),
            )
            new_project_id = int(cur.lastrowid)
            businesses = conn.execute(
                "SELECT id FROM businesses WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            ).fetchall()
            for business in businesses:
                self._duplicate_business(conn, int(business["id"]), new_project_id)
            return new_project_id

    def duplicate_business(
        self, business_id: int, target_project_id: int | None = None
    ) -> int:
        with self.transaction() as conn:
            return self._duplicate_business(conn, business_id, target_project_id)

    def _duplicate_business(
        self,
        conn: sqlite3.Connection,
        business_id: int,
        target_project_id: int | None = None,
    ) -> int:
        business = conn.execute(
            "SELECT * FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()
        if business is None:
            raise ValueError("사업을 찾을 수 없습니다")
        project_id = int(target_project_id or business["project_id"])
        cur = conn.execute(
            """
            INSERT INTO businesses(
                project_id, business_code, business_name, client,
                business_start_date, business_end_date
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                self._unique_business_copy_code(
                    conn,
                    project_id,
                    self._copy_text(business["business_code"], "사업코드"),
                ),
                self._copy_text(business["business_name"], "사업"),
                business["client"],
                business["business_start_date"],
                business["business_end_date"],
            ),
        )
        new_business_id = int(cur.lastrowid)
        reports = conn.execute(
            "SELECT id FROM reports WHERE business_id = ? ORDER BY id ASC",
            (business_id,),
        ).fetchall()
        for report in reports:
            self._duplicate_report(conn, int(report["id"]), new_business_id)
        return new_business_id

    def duplicate_report(
        self, report_id: int, target_business_id: int | None = None
    ) -> int:
        with self.transaction() as conn:
            return self._duplicate_report(conn, report_id, target_business_id)

    def _duplicate_report(
        self,
        conn: sqlite3.Connection,
        report_id: int,
        target_business_id: int | None = None,
    ) -> int:
        report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if report is None:
            raise ValueError("보고서를 찾을 수 없습니다")
        business_id = int(target_business_id or report["business_id"])
        payload = {field: report[field] for field in REPORT_FIELDS}
        payload["report_number"] = self._unique_report_copy_number(
            conn,
            business_id,
            self._copy_text(payload["report_number"], "보고서"),
        )
        columns = ["business_id"] + REPORT_FIELDS
        placeholders = ", ".join("?" for _ in columns)
        cur = conn.execute(
            f"INSERT INTO reports({', '.join(columns)}) VALUES ({placeholders})",
            tuple([business_id] + [payload[field] for field in REPORT_FIELDS]),
        )
        new_report_id = int(cur.lastrowid)
        self._ensure_report_children(conn, new_report_id)

        for role in ("upstream", "downstream"):
            manhole = conn.execute(
                "SELECT * FROM manholes WHERE report_id = ? AND role = ?",
                (report_id, role),
            ).fetchone()
            if manhole is None:
                continue
            values = [manhole[field] for field in MANHOLE_FIELDS]
            assignments = ", ".join(f"{field} = ?" for field in MANHOLE_FIELDS)
            conn.execute(
                f"UPDATE manholes SET {assignments} WHERE report_id = ? AND role = ?",
                tuple(values + [new_report_id, role]),
            )

        pipe = conn.execute(
            "SELECT * FROM pipe_information WHERE report_id = ?", (report_id,)
        ).fetchone()
        if pipe is not None:
            conn.execute(
                """
                UPDATE pipe_information
                SET length_m = ?, total_drive_distance_m = ?,
                    is_completed = ?, undriven_distance_m = ?
                WHERE report_id = ?
                """,
                (
                    pipe["length_m"],
                    pipe["total_drive_distance_m"],
                    pipe["is_completed"],
                    pipe["undriven_distance_m"],
                    new_report_id,
                ),
            )

        actual = conn.execute(
            "SELECT * FROM actual_survey_information WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if actual is not None:
            values = [actual[field] for field in ACTUAL_SURVEY_FIELDS]
            assignments = ", ".join(f"{field} = ?" for field in ACTUAL_SURVEY_FIELDS)
            conn.execute(
                f"UPDATE actual_survey_information SET {assignments} WHERE report_id = ?",
                tuple(values + [new_report_id]),
            )
        return new_report_id

    def update_manhole(
        self, report_id: int, role: str, payload: dict[str, object]
    ) -> None:
        self.ensure_report_children(report_id)
        values = [payload.get(field) for field in MANHOLE_FIELDS]
        assignments = ", ".join(f"{field} = ?" for field in MANHOLE_FIELDS)
        self.execute(
            f"UPDATE manholes SET {assignments} WHERE report_id = ? AND role = ?",
            tuple(values + [report_id, role]),
        )

    def get_manhole(self, report_id: int, role: str) -> Optional[sqlite3.Row]:
        self.ensure_report_children(report_id)
        return self.fetchone(
            "SELECT * FROM manholes WHERE report_id = ? AND role = ?",
            (report_id, role),
        )

    def update_pipe_information(
        self,
        report_id: int,
        length_m: float | None,
        total_drive_distance_m: float | None,
    ) -> None:
        self.ensure_report_children(report_id)
        is_completed, undriven = self.compute_pipe_completion(
            length_m, total_drive_distance_m
        )
        self.execute(
            """
            UPDATE pipe_information
            SET length_m = ?, total_drive_distance_m = ?,
                is_completed = ?, undriven_distance_m = ?
            WHERE report_id = ?
            """,
            (
                length_m,
                total_drive_distance_m,
                1 if is_completed else 0,
                undriven,
                report_id,
            ),
        )

    @staticmethod
    def compute_pipe_completion(
        length_m: float | None, total_drive_distance_m: float | None
    ) -> tuple[bool, float | None]:
        if length_m is None or total_drive_distance_m is None:
            return False, None
        undriven = length_m - total_drive_distance_m
        return abs(undriven) < 0.000001, undriven

    def get_pipe_information(self, report_id: int) -> Optional[sqlite3.Row]:
        self.ensure_report_children(report_id)
        return self.fetchone(
            "SELECT * FROM pipe_information WHERE report_id = ?", (report_id,)
        )

    def update_actual_survey(
        self, report_id: int, payload: dict[str, object]
    ) -> None:
        self.ensure_report_children(report_id)
        values = [payload.get(field) for field in ACTUAL_SURVEY_FIELDS]
        assignments = ", ".join(f"{field} = ?" for field in ACTUAL_SURVEY_FIELDS)
        self.execute(
            f"UPDATE actual_survey_information SET {assignments} WHERE report_id = ?",
            tuple(values + [report_id]),
        )

    def get_actual_survey(self, report_id: int) -> Optional[sqlite3.Row]:
        self.ensure_report_children(report_id)
        return self.fetchone(
            "SELECT * FROM actual_survey_information WHERE report_id = ?",
            (report_id,),
        )

    def upsert_video(
        self,
        report_id: int,
        file_path: str,
        duration: float | None,
        recorded_date: str | None,
        scan_direction: str,
    ) -> int:
        existing = self.get_video(report_id)
        if existing is None:
            return self.execute(
                """
                INSERT INTO report_videos(
                    report_id, file_path, duration, recorded_date, scan_direction
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_id, file_path, duration, recorded_date, scan_direction),
            )
        self.execute(
            """
            UPDATE report_videos
            SET file_path = ?, duration = ?, recorded_date = ?, scan_direction = ?
            WHERE report_id = ?
            """,
            (file_path, duration, recorded_date, scan_direction, report_id),
        )
        return int(existing["id"])

    def update_video_scan_direction(self, video_id: int, scan_direction: str) -> None:
        self.execute(
            "UPDATE report_videos SET scan_direction = ? WHERE id = ?",
            (scan_direction, video_id),
        )

    def update_video_depth_roi(
        self, video_id: int, roi_x: int, roi_y: int, roi_w: int, roi_h: int
    ) -> None:
        self.execute(
            """
            UPDATE report_videos
            SET depth_roi_x = ?, depth_roi_y = ?, depth_roi_w = ?, depth_roi_h = ?
            WHERE id = ?
            """,
            (roi_x, roi_y, roi_w, roi_h, video_id),
        )

    def get_video(self, report_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM report_videos WHERE report_id = ?", (report_id,)
        )

    def get_video_by_id(self, video_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM report_videos WHERE id = ?", (video_id,))

    def create_defect(
        self,
        report_id: int,
        video_id: int,
        timestamp_ms: int,
        image_path: str,
        drive_direction: str,
        distance_m: float | None,
        item_category: str | None,
        condition_item: str | None,
        defect_item: str | None,
        grade: str | None,
        quadrant: str | None,
        manhole_defect_depth_m: float | None,
        memo: str | None,
    ) -> int:
        return self.execute(
            """
            INSERT INTO report_defects(
                report_id, video_id, timestamp_ms, image_path, drive_direction,
                distance_m, item_category, condition_item, defect_item, grade,
                quadrant, manhole_defect_depth_m, memo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                video_id,
                timestamp_ms,
                image_path,
                drive_direction,
                distance_m,
                item_category,
                condition_item,
                defect_item,
                grade,
                quadrant,
                manhole_defect_depth_m,
                memo,
            ),
        )

    def update_defect(self, defect_id: int, payload: dict[str, object]) -> None:
        values = [payload.get(field) for field in DEFECT_FIELDS]
        assignments = ", ".join(f"{field} = ?" for field in DEFECT_FIELDS)
        self.execute(
            f"UPDATE report_defects SET {assignments} WHERE id = ?",
            tuple(values + [defect_id]),
        )

    def get_defect(self, defect_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM report_defects WHERE id = ?", (defect_id,))

    def delete_defect(self, defect_id: int) -> None:
        self.execute("DELETE FROM report_defects WHERE id = ?", (defect_id,))

    def list_defects(self, report_id: int) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT report_defects.*, report_videos.file_path AS video_file_path
            FROM report_defects
            JOIN report_videos ON report_videos.id = report_defects.video_id
            WHERE report_defects.report_id = ?
            ORDER BY report_defects.timestamp_ms ASC, report_defects.id ASC
            """,
            (report_id,),
        )

    def get_grade_counts(self, report_id: int) -> GradeCounts:
        rows = self.fetchall(
            """
            SELECT grade, COUNT(*) AS cnt
            FROM report_defects
            WHERE report_id = ?
            GROUP BY grade
            """,
            (report_id,),
        )
        grade_map = {"대": 0, "중": 0, "소": 0}
        total = 0
        for row in rows:
            grade = row["grade"]
            count = int(row["cnt"])
            if grade in grade_map:
                grade_map[grade] = count
                total += count
        return GradeCounts(
            large=grade_map["대"],
            medium=grade_map["중"],
            small=grade_map["소"],
            total=total,
        )
