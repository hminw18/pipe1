"""AI defect detection on the separate GPU server.

The worker hands the GPU server a signed URL to the playback copy; the server samples frames,
detects defects on each and links them into tracks (one track = one physical defect). Pipe1
stores the tracks as suggestions that an inspector confirms or rejects; the model never writes
a defect by itself.

The contract (the "Defect Detection API" requirements doc): POST /v1/jobs, then poll
GET /v1/jobs/{id} every 5 s for up to 3 hours (the worker accepts no callbacks). The GPU server
is the user's own machine, so a brief outage while polling is tolerated. Its result is
untrusted input, so it is validated and capped before anything is stored.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import quote

import httpx2
from pipe1_core.defect_taxonomy import MANHOLE_DEFECT_CODES, PIPE_DEFECT_CODES
from pydantic import AfterValidator, BaseModel, Field, model_validator

from pipe1_server.settings import Settings

logger = logging.getLogger(__name__)

SAMPLE_FPS = 5
POLL_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0
# A 30-minute video at 5 fps is 9,000 frames; allow for slow queues on the GPU server.
# Counted from submission, so it holds across worker restarts and Cloud Tasks retries.
JOB_TIMEOUT_SECONDS = 3 * 3600
# How long the GPU server may be unreachable while polling (a restart, a network drop).
UNREACHABLE_GRACE_SECONDS = 300.0
MAX_JOB_ID_LENGTH = 200
MAX_TRACKS = 2000
MAX_BOXES_PER_TRACK = 20000
MAX_BOXES = 200_000


def _valid_box(box: list[float]) -> list[float]:
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("box must be [x1, y1, x2, y2] within 0-1")
    return box


# [x1, y1, x2, y2], normalized to the frame.
Box = Annotated[list[float], Field(min_length=4, max_length=4), AfterValidator(_valid_box)]


class BoxSample(BaseModel):
    t: float = Field(ge=0)
    box: Box
    score: float | None = Field(default=None, ge=0, le=1)


class Track(BaseModel):
    code: str = Field(max_length=16)
    confidence: float = Field(ge=0, le=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    best_time_s: float | None = Field(default=None, ge=0)
    # Optional: a frame classifier reports time spans only.
    best_box: Box | None = None
    boxes: list[BoxSample] = Field(default_factory=list, max_length=MAX_BOXES_PER_TRACK)


class DetectionResult(BaseModel):
    model_version: str = Field(max_length=200)
    tracks: list[Track] = Field(max_length=MAX_TRACKS)

    @model_validator(mode="after")
    def _bounded(self) -> DetectionResult:
        if sum(len(track.boxes) for track in self.tracks) > MAX_BOXES:
            raise ValueError(f"more than {MAX_BOXES} boxes")
        return self


class DetectorError(RuntimeError):
    pass


class DetectorUnavailable(DetectorError):
    """The GPU server could not be reached; worth asking again shortly."""


@dataclass
class DetectorStatus:
    status: Literal["running", "succeeded", "failed"]
    progress: int | None = None
    result: DetectionResult | None = None
    error: str | None = None


class DefectDetector(Protocol):
    def submit(self, video_url: str) -> str: ...

    def poll(self, job_id: str) -> DetectorStatus: ...


def wait_for_result(
    detector: DefectDetector,
    job_id: str,
    submitted_at: float,
    progress: Callable[[int, str], None],
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> DetectionResult:
    """Poll until the GPU job ends. submitted_at is wall-clock time, so a resumed job keeps
    its original deadline."""
    unreachable_since: float | None = None
    while clock() < submitted_at + JOB_TIMEOUT_SECONDS:
        try:
            state = detector.poll(job_id)
        except DetectorUnavailable:
            if unreachable_since is None:
                unreachable_since = clock()
            if clock() - unreachable_since > UNREACHABLE_GRACE_SECONDS:
                raise
            logger.warning("detector job %s: server unreachable, retrying", job_id)
            sleep(POLL_INTERVAL_SECONDS)
            continue
        unreachable_since = None
        if state.status == "succeeded" and state.result is not None:
            return state.result
        if state.status == "failed":
            raise DetectorError(f"detector job {job_id} failed: {state.error}")
        if state.progress is not None:
            progress(state.progress, "AI 분석 중")
        sleep(POLL_INTERVAL_SECONDS)
    raise DetectorError(f"detector job {job_id} timed out")


# Codes on the detector's side follow docs/Defect_Taxonomy.md §4: pipe codes, manhole codes
# ending in "m", box-culvert codes as pipe codes ending in "c".
CODE_ITEMS: dict[str, tuple[str, str]] = {
    **{code: ("관로", item) for item, code in PIPE_DEFECT_CODES.items()},
    **{f"{code}c": ("암거", item) for item, code in PIPE_DEFECT_CODES.items()},
    **{code: ("맨홀", item) for item, code in MANHOLE_DEFECT_CODES.items()},
}


def item_for_code(code: str) -> tuple[str, str] | None:
    """(item_category, defect_item) for a detector code, or None if Pipe1 has no such item."""
    return CODE_ITEMS.get(code)


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _rounded(box: list[float]) -> list[float]:
    return [round(value, 4) for value in box]


def suggestion_rows(
    result: DetectionResult, duration_seconds: float | None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """DefectSuggestion column values for each track Pipe1 can record, and the number of
    tracks dropped per unknown code. Times are clamped to the video."""
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for track in result.tracks:
        item = item_for_code(track.code)
        if item is None:
            skipped[track.code] = skipped.get(track.code, 0) + 1
            continue
        start, end = sorted((track.start_s, track.end_s))
        if duration_seconds:
            end = min(end, duration_seconds)
            start = min(start, end)
        best = track.best_time_s if track.best_time_s is not None else (start + end) / 2
        best = min(max(best, start), end)
        boxes = sorted(track.boxes, key=lambda sample: sample.t)
        best_box = track.best_box
        if best_box is None and boxes:
            best_box = min(boxes, key=lambda sample: abs(sample.t - best)).box
        rows.append(
            {
                "code": track.code,
                "item_category": item[0],
                "defect_item": item[1],
                "confidence": track.confidence,
                "start_ms": _ms(start),
                "end_ms": _ms(end),
                "best_ms": _ms(best),
                "best_box": _rounded(best_box) if best_box else None,
                "boxes": [
                    {"t_ms": _ms(sample.t), "box": _rounded(sample.box), "score": sample.score}
                    for sample in boxes
                ],
            }
        )
    return rows, skipped


class HttpDefectDetector:
    def __init__(
        self, base_url: str, token: str | None, *, client: httpx2.Client | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        url = f"{self._url}{path}"
        if self._client is not None:
            response = self._client.request(method, url, headers=self._headers, **kwargs)
        else:
            with httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.request(method, url, headers=self._headers, **kwargs)
        # Any non-2xx fails the job (the contract); only an unreachable server is retried.
        response.raise_for_status()
        return response

    def submit(self, video_url: str) -> str:
        response = self._request(
            "POST", "/v1/jobs", json={"video_url": video_url, "sample_fps": SAMPLE_FPS}
        )
        job_id = response.json().get("job_id")
        if not isinstance(job_id, str | int) or not 0 < len(str(job_id)) <= MAX_JOB_ID_LENGTH:
            raise DetectorError("detector returned no usable job_id")
        return str(job_id)

    def poll(self, job_id: str) -> DetectorStatus:
        try:
            response = self._request("GET", f"/v1/jobs/{quote(job_id, safe='')}")
        except httpx2.TransportError as error:
            raise DetectorUnavailable(str(error)) from error
        body = response.json()
        status = body.get("status")
        if status == "succeeded":
            return DetectorStatus(
                "succeeded", result=DetectionResult.model_validate(body["result"])
            )
        if status == "failed":
            return DetectorStatus("failed", error=str(body.get("error"))[:500])
        value = body.get("progress")
        return DetectorStatus(
            "running", progress=int(value) if isinstance(value, int | float) else None
        )


class SampleDefectDetector:
    """Development: returns a stored result in the GPU server's format, whatever the video."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def submit(self, video_url: str) -> str:
        return "sample"

    def poll(self, job_id: str) -> DetectorStatus:
        result = DetectionResult.model_validate_json(self._path.read_bytes())
        return DetectorStatus("succeeded", result=result)


def build_detector(settings: Settings) -> DefectDetector | None:
    if settings.defect_detector_url:
        token = settings.defect_detector_token
        return HttpDefectDetector(
            settings.defect_detector_url, token.get_secret_value() if token else None
        )
    if settings.defect_detector_sample_file:
        return SampleDefectDetector(Path(settings.defect_detector_sample_file))
    return None
