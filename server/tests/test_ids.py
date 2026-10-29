import time

from pipe1_server.ids import uuid7


def test_uuid7_sorts_by_creation_time() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second
