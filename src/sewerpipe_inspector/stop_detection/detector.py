from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from .config import StopDetectionConfig
from .utils import mean, merge_close_segments


class StopSegmentDetector:
    def __init__(self, config: StopDetectionConfig | None = None) -> None:
        self.config = config or StopDetectionConfig()
        self.logger = logging.getLogger(self.__class__.__name__)

    def analyze(self, video_path: str) -> list[dict[str, float]]:
        started = time.perf_counter()
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(str(path))

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        sample_fps = max(0.1, float(self.config.fps))
        if native_fps > 0:
            sample_step = max(1, int(round(native_fps / sample_fps)))
        else:
            sample_step = 1

        motions: list[float] = []
        motion_times: list[float] = []

        prev_gray: np.ndarray | None = None
        frame_index = 0
        sampled_index = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % sample_step != 0:
                frame_index += 1
                continue

            gray = self._preprocess_frame(frame)
            timestamp = (
                frame_index / native_fps
                if native_fps > 0
                else sampled_index / sample_fps
            )

            if prev_gray is not None:
                motion = self._compute_motion(prev_gray, gray)
                motions.append(motion)
                motion_times.append(timestamp)

            prev_gray = gray
            sampled_index += 1
            frame_index += 1

        cap.release()

        if not motions:
            self.logger.info("No sampled motions from video: %s", video_path)
            return []

        baseline = self._compute_baseline(motions, motion_times)
        t_stop = baseline * float(self.config.stop_threshold_factor)
        t_move = max(
            baseline * float(self.config.move_threshold_factor),
            t_stop * 1.35,
        )

        smoothed_motions = self._smooth_motions(motions)

        min_required = max(1.0, float(self.config.min_stop_duration))
        raw_segments = self._extract_segments(
            motions=smoothed_motions,
            motion_times=motion_times,
            t_stop=t_stop,
            t_move=t_move,
        )
        merged = merge_close_segments(raw_segments, self.config.merge_gap_threshold)
        merged = [seg for seg in merged if seg["duration"] >= min_required]

        max_dur = self.config.max_stop_duration
        if max_dur is not None:
            merged = [seg for seg in merged if seg["duration"] <= max_dur]

        elapsed = time.perf_counter() - started
        self.logger.info(
            "Stop detection done: segments=%d baseline=%.6f t_stop=%.6f t_move=%.6f time=%.3fs",
            len(merged),
            baseline,
            t_stop,
            t_move,
            elapsed,
        )
        return merged

    def _smooth_motions(self, motions: list[float]) -> list[float]:
        if not motions:
            return []
        alpha = min(1.0, max(0.05, float(self.config.smoothing_alpha)))
        output: list[float] = []
        ema = motions[0]
        for m in motions:
            ema = (alpha * m) + ((1.0 - alpha) * ema)
            output.append(float(ema))
        return output

    def _preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        target_w = max(64, int(self.config.resize_width))
        if w != target_w:
            target_h = max(32, int((target_w / max(1, w)) * h))
            frame = cv2.resize(
                frame, (target_w, target_h), interpolation=cv2.INTER_AREA
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def _compute_motion(self, prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
        if self.config.use_optical_flow:
            init_flow = np.zeros(
                (prev_gray.shape[0], prev_gray.shape[1], 2),
                dtype=np.float32,
            )
            flow = cv2.calcOpticalFlowFarneback(
                prev_gray,
                curr_gray,
                init_flow,
                0.5,
                3,
                15,
                3,
                5,
                1.2,
                0,
            )
            mag, _ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            return float(np.mean(mag))

        diff = cv2.absdiff(prev_gray, curr_gray)
        return float(np.mean(diff))

    def _compute_baseline(
        self, motions: list[float], motion_times: list[float]
    ) -> float:
        baseline_window = [
            m
            for m, ts in zip(motions, motion_times)
            if ts <= float(self.config.baseline_seconds)
        ]
        baseline = max(
            self._robust_motion_baseline(baseline_window),
            self._robust_motion_baseline(motions),
        )
        return max(baseline, 1e-6)

    def _robust_motion_baseline(self, motions: list[float]) -> float:
        moving_like = [m for m in motions if m > 1e-6]
        source = moving_like if moving_like else motions
        if not source:
            return 0.0
        if len(source) < 4:
            return mean(source)
        values = np.array(source, dtype=np.float32)
        return float(np.percentile(values, 70))

    def _extract_segments(
        self,
        motions: list[float],
        motion_times: list[float],
        t_stop: float,
        t_move: float,
    ) -> list[dict[str, float]]:
        segments: list[dict[str, float]] = []
        seg_start: float | None = None
        candidate_start: float | None = None
        move_candidate_start: float | None = None
        state = "MOVING"
        low_streak = 0
        moving_streak = 0
        enter_frames = max(1, int(self.config.stop_enter_frames))
        release_frames = max(1, int(self.config.stop_release_frames))

        for motion, ts in zip(motions, motion_times):
            if state == "MOVING":
                if motion < t_stop:
                    if low_streak == 0:
                        candidate_start = ts
                    low_streak += 1
                    if low_streak >= enter_frames:
                        state = "STOPPED"
                        seg_start = candidate_start
                        moving_streak = 0
                else:
                    low_streak = 0
                    candidate_start = None
                continue

            if motion > t_move:
                if moving_streak == 0:
                    move_candidate_start = ts
                moving_streak += 1
                if moving_streak >= release_frames and seg_start is not None:
                    end_time = move_candidate_start if move_candidate_start is not None else ts
                    duration = end_time - seg_start
                    if duration > 0:
                        segments.append(
                            {
                                "start_time": float(seg_start),
                                "end_time": float(end_time),
                                "duration": float(duration),
                            }
                        )
                    seg_start = None
                    candidate_start = None
                    move_candidate_start = None
                    state = "MOVING"
                    low_streak = 0
                    moving_streak = 0
            else:
                moving_streak = 0
                move_candidate_start = None

        if seg_start is not None:
            end_time = motion_times[-1]
            duration = end_time - seg_start
            if duration > 0:
                segments.append(
                    {
                        "start_time": float(seg_start),
                        "end_time": float(end_time),
                        "duration": float(duration),
                    }
                )

        return segments
