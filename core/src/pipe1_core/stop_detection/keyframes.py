"""Keyframe-only decoding through the ffmpeg command-line tool.

OpenCV can only step through every frame: each H.264 frame depends on the ones before it, so
reading one frame per second still decodes all of them. ffmpeg can skip everything except
keyframes (typically one every 1-2 seconds), which is a small fraction of the decoding work.
"""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np

_PTS_TIME = re.compile(rb"\bpts_time:\s*(-?[0-9.]+)")
_TIMESTAMP_WAIT_SECONDS = 120


class KeyframeError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def keyframe_crops(
    path: str | Path,
    roi: tuple[int, int, int, int],
    min_spacing_seconds: float,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (seconds, BGR crop of roi) for each keyframe, at most one per min_spacing_seconds.

    The spacing limit matters for all-intra sources (MJPEG), where every frame is a keyframe.
    roi must lie inside the frame. Raises KeyframeError if ffmpeg fails.
    """
    x, y, w, h = roi
    frame_size = w * h * 3
    select = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{min_spacing_seconds:.3f})'"
    args = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",  # showinfo logs at info level
        "-protocol_whitelist",
        "file,pipe",
        "-skip_frame",
        "nokey",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"{select},format=bgr24,crop={w}:{h}:{x}:{y},showinfo",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    process = subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert process.stdout is not None and process.stderr is not None
    timestamps: queue.Queue[float | None] = queue.Queue()
    last_lines: list[bytes] = []

    def read_log() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if b"Parsed_showinfo" in line:
                match = _PTS_TIME.search(line)
                if match:
                    timestamps.put(float(match.group(1)))
            else:
                last_lines[:] = [*last_lines[-4:], line.strip()]
        timestamps.put(None)

    reader = threading.Thread(target=read_log, daemon=True)
    reader.start()
    try:
        while True:
            data = process.stdout.read(frame_size)
            if not data:
                break
            if len(data) != frame_size:
                raise KeyframeError("truncated frame from ffmpeg")
            try:
                timestamp = timestamps.get(timeout=_TIMESTAMP_WAIT_SECONDS)
            except queue.Empty as error:
                raise KeyframeError("ffmpeg reported no timestamp") from error
            if timestamp is None:
                raise KeyframeError("ffmpeg reported no timestamp")
            yield timestamp, np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3).copy()
        if process.wait() != 0:
            detail = last_lines[-1].decode("utf-8", "replace") if last_lines else ""
            raise KeyframeError(f"ffmpeg failed: {detail[:300]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=5)
