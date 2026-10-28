"""after_commit callbacks run only after a successful commit, and see the committed data."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.db import after_commit, get_db, get_session_factory


def _app(engine: Engine, seen: list[object]) -> TestClient:
    app = FastAPI()
    factory = sessionmaker(engine)
    app.dependency_overrides[get_session_factory] = lambda: factory

    def visible(marker: str) -> None:
        with factory() as other:
            seen.append(
                other.execute(
                    text("SELECT count(*) FROM login_throttle WHERE key = :k"), {"k": marker}
                ).scalar()
            )

    @app.post("/ok/{marker}")
    def ok(marker: str, db: Session = Depends(get_db)) -> dict:  # noqa: B008
        db.execute(text("INSERT INTO login_throttle(key) VALUES (:k)"), {"k": marker})
        after_commit(db, visible, marker)
        return {}

    @app.post("/fail/{marker}")
    def fail(marker: str, db: Session = Depends(get_db)) -> dict:  # noqa: B008
        db.execute(text("INSERT INTO login_throttle(key) VALUES (:k)"), {"k": marker})
        after_commit(db, visible, marker)
        raise HTTPException(500)

    return TestClient(app, raise_server_exceptions=False)


def test_callbacks_see_committed_rows(app_engine: Engine) -> None:
    seen: list[object] = []
    client = _app(app_engine, seen)
    assert client.post("/ok/after-commit-ok").status_code == 200
    assert seen == [1]


def test_callbacks_skipped_on_rollback(app_engine: Engine) -> None:
    seen: list[object] = []
    client = _app(app_engine, seen)
    assert client.post("/fail/after-commit-fail").status_code == 500
    assert seen == []
