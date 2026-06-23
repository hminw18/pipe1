__all__ = ["run"]


def run() -> None:
    from .app import run as _run

    _run()
