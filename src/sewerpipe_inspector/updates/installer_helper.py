from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


HELPER_FLAG = "--pipe1-apply-update"
SUCCESS_EXIT_CODES = {0, 1641, 3010}


def maybe_run_packaged_helper(argv: Sequence[str] | None = None) -> int | None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != HELPER_FLAG:
        return None
    return main(args[1:])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a PIPE1 MSI update.")
    parser.add_argument("--msi", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--wait-timeout-seconds", type=int, default=90)
    parser.add_argument("--show-failure-dialog", action="store_true")
    args = parser.parse_args(argv)

    msi_path = Path(args.msi)
    log_path = Path(args.log)
    result_path = Path(args.result)
    started_at = _now()
    wait_completed = True
    exit_code: int | None = None
    error: str | None = None

    try:
        if args.wait_pid:
            wait_completed = wait_for_pid(args.wait_pid, args.wait_timeout_seconds)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        exit_code = run_msiexec(msi_path, log_path)
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__

    success = error is None and exit_code in SUCCESS_EXIT_CODES
    payload = {
        "started_at": started_at,
        "finished_at": _now(),
        "msi_path": str(msi_path),
        "log_path": str(log_path),
        "wait_pid": args.wait_pid or None,
        "wait_completed": wait_completed,
        "exit_code": exit_code,
        "success": success,
        "error": error,
    }
    _write_result(result_path, payload)

    if not success and args.show_failure_dialog:
        _show_failure_dialog(result_path, log_path, exit_code, error)
    return 0 if success else 1


def wait_for_pid(pid: int, timeout_seconds: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        return _wait_for_windows_pid(pid, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


def run_msiexec(msi_path: Path, log_path: Path) -> int:
    if not msi_path.exists() or msi_path.suffix.lower() != ".msi":
        raise FileNotFoundError(str(msi_path))
    args = [
        "/i",
        str(msi_path),
        "/quiet",
        "/norestart",
        "/L*v",
        str(log_path),
    ]
    if os.name == "nt":
        return _run_windows_msiexec_elevated(args)
    completed = subprocess.run(["msiexec", *args], check=False)
    return int(completed.returncode)


def _wait_for_windows_pid(pid: int, timeout_seconds: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True
    try:
        timeout_ms = max(0, timeout_seconds) * 1000
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == wait_object_0:
            return True
        if result == wait_timeout:
            return False
        return False
    finally:
        kernel32.CloseHandle(handle)


def _run_windows_msiexec_elevated(args: list[str]) -> int:
    import ctypes.wintypes as wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    see_mask_nocloseprocess = 0x00000040
    infinite = 0xFFFFFFFF
    show_hidden = 0
    params = subprocess.list2cmdline(args)
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = see_mask_nocloseprocess
    info.lpVerb = "runas"
    info.lpFile = "msiexec.exe"
    info.lpParameters = params
    info.nShow = show_hidden

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        kernel32.WaitForSingleObject(info.hProcess, infinite)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _show_failure_dialog(
    result_path: Path,
    log_path: Path,
    exit_code: int | None,
    error: str | None,
) -> None:
    message = (
        "PIPE1 update installation failed.\n\n"
        f"Exit code: {exit_code if exit_code is not None else '-'}\n"
        f"Error: {error or '-'}\n\n"
        f"Log: {log_path}\n"
        f"Result: {result_path}"
    )
    if os.name != "nt":
        print(message, file=sys.stderr)
        return
    ctypes.windll.user32.MessageBoxW(None, message, "PIPE1 Update", 0x00000010)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
