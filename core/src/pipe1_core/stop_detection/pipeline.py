"""Full stop analysis: depth-OCR stop segments, then candidate frames per segment.

Moved out of the desktop UI module (ui/main_window.py) so the desktop worker thread and the
web worker run the same code.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .depth_ocr_detector import DepthOcrStopDetectionConfig, DepthOcrStopSegmentDetector
from .frame_candidate_detector import (
    StopFrameCandidateDetectionConfig,
    StopFrameCandidateDetector,
)
from .video_capture import open_analysis_video_capture

ProgressCallback = Callable[[int, str], None]

DEPTH_CONFIG = DepthOcrStopDetectionConfig(
    fps=1.0,
    min_stop_duration=3.0,
    stop_distance_tolerance_m=0.5,
    merge_gap_threshold=1.0,
    max_missing_bridge_seconds=3.0,
    min_digit_score=0.5,
)
CANDIDATE_CONFIG = StopFrameCandidateDetectionConfig(
    fps=5.0,
    max_candidates_per_segment=3,
    min_stable_duration=0.6,
    min_candidate_gap=1.2,
)
MAX_CANDIDATE_WORKERS = 8


def default_worker_count() -> int:
    """Each worker opens its own decoder; never oversubscribe the machine."""
    return max(1, min(MAX_CANDIDATE_WORKERS, os.cpu_count() or 1))


@dataclass
class StopAnalysisResult:
    segments: list[dict[str, object]]
    benchmark: dict[str, object] = field(default_factory=dict)
    # When the camera set off into the pipe (the depth counter first moved), in seconds.
    travel_start_s: float | None = None
    # (seconds, metres or None when unreadable), one per second of video.
    readings: list[tuple[float, float | None]] = field(default_factory=list)


def analyze_candidate_segment(video_path: str, segment: dict[str, float]) -> dict[str, object]:
    detector = StopFrameCandidateDetector(CANDIDATE_CONFIG)
    cap = open_analysis_video_capture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")
    try:
        read_started = time.perf_counter()
        samples = detector.read_segment_samples(cap, segment)
        read_seconds = time.perf_counter() - read_started
    finally:
        cap.release()

    select_started = time.perf_counter()
    candidates = detector.select_candidates(samples, segment)
    select_seconds = time.perf_counter() - select_started

    output: dict[str, object] = dict(segment)
    output["candidates"] = candidates
    output["analysis_seconds"] = read_seconds + select_seconds
    output["candidate_read_seconds"] = read_seconds
    output["candidate_select_seconds"] = select_seconds
    output["candidate_sample_count"] = len(samples)
    return output


def run_stop_analysis(
    video_path: str,
    depth_roi: tuple[int, int, int, int],
    progress: ProgressCallback | None = None,
    max_workers: int | None = None,
) -> StopAnalysisResult:
    """Depth OCR finds where the camera stopped (0-45%), then candidate frames (45-100%).

    Raises if the depth stage fails; candidate failures are reported per result.
    """

    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    total_started = time.perf_counter()
    benchmark: dict[str, object] = {
        "depth_seconds": 0.0,
        "candidate_seconds": 0.0,
        "total_seconds": 0.0,
        "candidate_error": None,
    }

    def depth_progress(current: int, total: int, message: str) -> None:
        report(int(max(0, min(45, (current / max(1, total)) * 45))), message)

    report(0, "거리 OCR 준비")
    started = time.perf_counter()
    depth_detector = DepthOcrStopSegmentDetector(DEPTH_CONFIG)
    stop_segments = depth_detector.analyze(
        video_path,
        depth_roi,
        progress_callback=depth_progress,
        max_workers=max_workers or default_worker_count(),
    )
    benchmark["depth_seconds"] = time.perf_counter() - started
    benchmark["depth"] = depth_detector.last_stats

    candidate_started = time.perf_counter()
    analyzed: list[dict[str, object]] = []
    candidate_error: str | None = None
    workers = max(1, min(max_workers or default_worker_count(), len(stop_segments) or 1))
    try:
        total = len(stop_segments)
        report(45, f"후보 프레임 병렬 분석 준비 · 의심구간 {total}개 · 워커 {workers}개")
        if total:
            results: dict[int, dict[str, object]] = {}
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(analyze_candidate_segment, video_path, segment): index
                    for index, segment in enumerate(stop_segments)
                }
                for completed, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    results[futures[future]] = result
                    report(
                        45 + int((completed / total) * 55),
                        f"후보 프레임 병렬 분석 {completed}/{total} 완료"
                        f" · 최근 {float(result.get('analysis_seconds', 0.0)):.2f}s",
                    )
            analyzed = [results[index] for index in range(total)]
            seconds = [float(segment.get("analysis_seconds", 0.0)) for segment in analyzed]
            benchmark["candidate_workers"] = workers
            benchmark["candidate_segment_seconds_sum"] = sum(seconds)
            benchmark["candidate_slowest_seconds"] = max(seconds, default=0.0)
            benchmark["candidate_fastest_seconds"] = min(seconds, default=0.0)
    except Exception as exc:  # candidate stage is best-effort; segments are still useful
        candidate_error = str(exc)
        analyzed = [
            dict(segment, candidates=[], analysis_error=candidate_error)
            for segment in stop_segments
        ]

    benchmark["candidate_seconds"] = time.perf_counter() - candidate_started
    benchmark["candidate_error"] = candidate_error
    benchmark["total_seconds"] = time.perf_counter() - total_started
    report(100, "의심구간 분석 완료")
    return StopAnalysisResult(
        analyzed, benchmark, depth_detector.last_travel_start, depth_detector.last_readings
    )
