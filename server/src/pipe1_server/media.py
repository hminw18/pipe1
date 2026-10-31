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
import struct
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
# Frames captured in the browser (8K is the largest recorder output we expect).
MAX_FRAME_BYTES = 48 * 1024 * 1024
MAX_FRAME_SIDE = 8192
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
    pix_fmt: str = ""

    @property
    def browser_playable(self) -> bool:
        """8-bit 4:2:0 H.264 plays in every browser once it is in an MP4 with the index first,
        so it only needs repackaging, not re-encoding (most CCTV recorders produce it)."""
        return self.codec == "h264" and self.pix_fmt in ("yuv420p", "yuvj420p")


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
    return ProbeResult(
        format_name,
        str(video.get("codec_name", "")),
        duration,
        fps,
        width,
        height,
        str(video.get("pix_fmt", "")),
    )


def remux_for_browser(source: str, destination: Path) -> None:
    """Copy the H.264 stream into an MP4 with the index at the front: no re-encoding, so it
    takes about as long as reading the file once."""
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            # AVI and some recorder files carry no presentation timestamps.
            "-fflags",
            "+genpts",
            *_input_args(source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        TRANSCODE_TIMEOUT_SECONDS,
    )


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
            # A keyframe every 2 s: quick seeking in the browser and in stop analysis.
            "-force_key_frames",
            "expr:gte(t,n_forced*2)",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        TRANSCODE_TIMEOUT_SECONDS,
    )


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


def normalize_frame_png(data: bytes) -> bytes:
    """A frame the browser captured from the user's local file. Accept only a PNG of sane
    dimensions (checked in the header, before decoding) and re-encode it, so only pixels are
    stored."""
    import cv2
    import numpy as np

    if len(data) > MAX_FRAME_BYTES or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise MediaError("not a PNG frame")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= MAX_FRAME_SIDE and 0 < height <= MAX_FRAME_SIDE):
        raise MediaError("frame dimensions out of range")
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise MediaError("unreadable frame")
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise MediaError("could not encode frame")
    return encoded.tobytes()


def read_distance(png: bytes, roi: tuple[int, int, int, int]) -> float | None:
    """Depth counter reading in the region of interest (text recognizer, see text_reader.py)."""
    import cv2
    import numpy as np
    from pipe1_core.services.ocr_service import OCRService

    frame = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    value = OCRService().read_depth_value(frame, roi)
    return abs(float(value)) if value is not None else None
