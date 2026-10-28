"""ffprobe/ffmpeg operations for the worker (docs/Web_Architecture.md §4).

Uploaded videos are untrusted input to complex parsers, so every invocation:
- runs without a shell, with a timeout;
- restricts demuxers to the accepted container formats (-format_whitelist), so a file
  disguised as a playlist or image sequence cannot make ffmpeg read other files;
- restricts protocols (-protocol_whitelist) to local files, or to HTTPS for signed URLs.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

ACCEPTED_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4"}
# ffprobe format names for the accepted containers.
ACCEPTED_FORMATS = ("avi", "matroska", "webm", "mov", "mp4", "m4a", "3gp", "3g2", "mj2")
FORMAT_WHITELIST = ",".join(ACCEPTED_FORMATS)
FILE_PROTOCOLS = "file,pipe"
URL_PROTOCOLS = "https,tls,tcp,crypto,file,pipe"
PROBE_TIMEOUT_SECONDS = 120
TRANSCODE_TIMEOUT_SECONDS = 4 * 3600
FRAME_TIMEOUT_SECONDS = 60
THUMBNAIL_WIDTH = 160
MAX_THUMBNAIL_TILES = 200


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    format_name: str
    codec: str
    duration_seconds: float
    fps: float
    width: int
    height: int


def _protocols(source: str) -> str:
    return URL_PROTOCOLS if source.startswith("https://") else FILE_PROTOCOLS


def _run(args: list[str], timeout: int) -> bytes:
    try:
        completed = subprocess.run(
            args, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as error:
        raise MediaError(f"{args[0]} timed out") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise MediaError(f"{args[0]} failed: {detail[0][:300]}")
    return completed.stdout


def _input_args(source: str) -> list[str]:
    return [
        "-protocol_whitelist",
        _protocols(source),
        "-format_whitelist",
        FORMAT_WHITELIST,
        "-i",
        source,
    ]


def _fraction(text: str | None) -> float:
    if not text:
        return 0.0
    numerator, _, denominator = text.partition("/")
    try:
        value = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def probe(source: str) -> ProbeResult:
    """Validate that the source is an accepted container with a video stream."""
    output = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            _protocols(source),
            "-format_whitelist",
            FORMAT_WHITELIST,
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            source,
        ],
        PROBE_TIMEOUT_SECONDS,
    )
    try:
        info = json.loads(output)
    except json.JSONDecodeError as error:
        raise MediaError("unreadable probe output") from error
    format_name = str(info.get("format", {}).get("format_name", ""))
    if not set(format_name.split(",")) & set(ACCEPTED_FORMATS):
        raise MediaError(f"unsupported container: {format_name or 'unknown'}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("no video stream")
    duration = _fraction(str(info.get("format", {}).get("duration") or video.get("duration")))
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0:
        raise MediaError("video has no duration or size")
    return ProbeResult(format_name, str(video.get("codec_name", "")), duration, fps, width, height)


def transcode_for_browser(source: str, destination: Path) -> None:
    """H.264 MP4 with the index at the front, so browsers can seek with range requests."""
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            *_input_args(source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        TRANSCODE_TIMEOUT_SECONDS,
    )


def thumbnail_interval(duration_seconds: float) -> float:
    return max(1.0, math.ceil(duration_seconds / MAX_THUMBNAIL_TILES))


def thumbnail_strip(source: str, destination: Path, duration_seconds: float) -> float:
    """One JPEG with a tile every interval seconds, left to right. Returns the interval."""
    interval = thumbnail_interval(duration_seconds)
    tiles = max(1, min(MAX_THUMBNAIL_TILES, math.ceil(duration_seconds / interval)))
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            *_input_args(source),
            "-vf",
            f"fps=1/{interval},scale={THUMBNAIL_WIDTH}:-2,tile={tiles}x1",
            "-frames:v",
            "1",
            "-q:v",
            "5",
            str(destination),
        ],
        TRANSCODE_TIMEOUT_SECONDS,
    )
    return interval


def extract_frame_png(source: str, timestamp_ms: int) -> bytes:
    """Full-resolution frame at timestamp_ms as PNG. Seeking is time-based (-ss before -i
    is frame-accurate in current ffmpeg), so captures match the browser's position."""
    if timestamp_ms < 0:
        raise MediaError("negative timestamp")
    return _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{timestamp_ms / 1000:.3f}",
            *_input_args(source),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "pipe:1",
        ],
        FRAME_TIMEOUT_SECONDS,
    )


def read_distance(png: bytes, roi: tuple[int, int, int, int]) -> float | None:
    """Depth counter reading in the region of interest (desktop OCR, template matching)."""
    import cv2
    import numpy as np
    from pipe1_core.services.ocr_service import OCRService

    frame = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    value = OCRService().read_depth_value(frame, roi)
    return abs(float(value)) if value is not None else None
