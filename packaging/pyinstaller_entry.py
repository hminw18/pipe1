from __future__ import annotations

from sewerpipe_inspector.updates.installer_helper import maybe_run_packaged_helper


if __name__ == "__main__":
    helper_exit_code = maybe_run_packaged_helper()
    if helper_exit_code is not None:
        raise SystemExit(helper_exit_code)

    from sewerpipe_inspector.app import run

    run()
