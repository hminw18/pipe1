from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .video_capture import open_analysis_video_capture


@dataclass
class StopFrameCandidateDetectionConfig:
    fps: float = 5.0
    resize_width: int = 480
    max_candidates_per_segment: int = 3
    min_stable_duration: float = 0.6
    stable_motion_median_window: int = 3
    min_candidate_gap: float = 1.2
    duplicate_time_window: float = 4.0
    duplicate_hash_threshold: float = 0.18
    pre_motion_window: float = 2.0
    post_motion_window: float = 1.2
    edge_guard_seconds: float = 0.8
    min_confidence: float = 0.45
    stable_percentile: float = 30.0
    moving_percentile: float = 75.0
    motion_burst_percentile: float = 70.0


@dataclass(frozen=True)
class MotionSample:
    time: float
    motion: float | None
    sharpness: float
    rotation_motion: float = 0.0
    translation_motion: float = 0.0
    signature: np.ndarray | None = None


class StopFrameCandidateDetector:
    def __init__(
        self, config: StopFrameCandidateDetectionConfig | None = None
    ) -> None:
        self.config = config or StopFrameCandidateDetectionConfig()
        self.logger = logging.getLogger(self.__class__.__name__)

    def analyze(
        self,
        video_path: str,
        stop_segments: list[dict[str, float]],
    ) -> list[dict[str, object]]:
        started = time.perf_counter()
        if not stop_segments:
            return []

        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(str(path))

        cap = open_analysis_video_capture(path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        try:
            segments: list[dict[str, object]] = []
            for segment in stop_segments:
                samples = self._read_segment_samples(cap, segment)
                candidates = self._select_candidates(samples, segment)
                output = dict(segment)
                output["candidates"] = candidates
                segments.append(output)
        finally:
            cap.release()

        elapsed = time.perf_counter() - started
        count = sum(len(seg.get("candidates", [])) for seg in segments)
        self.logger.info(
            "Stop frame candidate detection done: segments=%d candidates=%d time=%.3fs",
            len(segments),
            count,
            elapsed,
        )
        return segments

    def _read_segment_samples(
        self,
        cap: cv2.VideoCapture,
        segment: dict[str, float],
    ) -> list[MotionSample]:
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if native_fps <= 0:
            native_fps = 30.0

        sample_fps = max(0.1, float(self.config.fps))
        sample_step = max(1, int(round(native_fps / sample_fps)))
        start_time = max(0.0, float(segment["start_time"]))
        end_time = max(start_time, float(segment["end_time"]))
        start_frame = max(0, int(start_time * native_fps))
        end_frame = max(start_frame, int(end_time * native_fps))

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_index = start_frame
        previous_gray: np.ndarray | None = None
        previous_mask: np.ndarray | None = None
        samples: list[MotionSample] = []

        while frame_index <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break

            if (frame_index - start_frame) % sample_step == 0:
                gray, mask, signature = self._preprocess_frame(frame)
                timestamp = frame_index / native_fps
                motion: float | None = None
                rotation_motion = 0.0
                translation_motion = 0.0
                if previous_gray is not None and previous_mask is not None:
                    motion = self._compute_motion(previous_gray, gray, previous_mask)
                    rotation_motion, translation_motion = self._compute_motion_types(
                        previous_gray,
                        gray,
                        previous_mask,
                        motion,
                    )
                samples.append(
                    MotionSample(
                        time=float(timestamp),
                        motion=motion,
                        sharpness=self._compute_sharpness(gray, mask),
                        rotation_motion=float(rotation_motion),
                        translation_motion=float(translation_motion),
                        signature=signature,
                    )
                )
                previous_gray = gray
                previous_mask = mask

            frame_index += 1

        return samples

    def _preprocess_frame(
        self, frame: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        h, w = frame.shape[:2]
        target_w = max(64, int(self.config.resize_width))
        if w != target_w:
            target_h = max(32, int((target_w / max(1, w)) * h))
            frame = cv2.resize(
                frame, (target_w, target_h), interpolation=cv2.INTER_AREA
            )

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = np.ones(gray.shape, dtype=np.uint8)
        mask_h, mask_w = gray.shape[:2]
        mask[0 : int(mask_h * 0.20), 0 : int(mask_w * 0.45)] = 0
        mask[0 : int(mask_h * 0.20), int(mask_w * 0.68) : mask_w] = 0
        mask[int(mask_h * 0.78) : mask_h, 0 : int(mask_w * 0.28)] = 0
        return gray, mask, self._compute_signature(gray, mask)

    def _compute_motion(
        self, previous_gray: np.ndarray, gray: np.ndarray, mask: np.ndarray
    ) -> float:
        diff = cv2.absdiff(previous_gray, gray)
        values = diff[mask > 0]
        if values.size == 0:
            values = diff.reshape(-1)
        return float(np.mean(values))

    def _compute_motion_types(
        self,
        previous_gray: np.ndarray,
        gray: np.ndarray,
        mask: np.ndarray,
        pixel_motion: float,
    ) -> tuple[float, float]:
        if pixel_motion <= 0:
            return 0.0, 0.0

        flow = cv2.calcOpticalFlowFarneback(
            previous_gray,
            gray,
            None,
            0.5,
            3,
            21,
            3,
            5,
            1.2,
            0,
        )
        fx = flow[..., 0]
        fy = flow[..., 1]
        h, w = gray.shape[:2]
        ys, xs = np.indices((h, w), dtype=np.float32)
        cx = (w - 1) / 2.0
        cy = (h - 1) / 2.0
        rx = xs - cx
        ry = ys - cy
        radius = np.sqrt((rx * rx) + (ry * ry))
        valid = (mask > 0) & (radius > max(4.0, min(h, w) * 0.08))
        if not np.any(valid):
            return pixel_motion, 0.0

        mag = np.sqrt((fx * fx) + (fy * fy))
        mean_mag = float(np.mean(mag[valid]))
        if mean_mag <= 1e-6:
            return 0.0, 0.0

        radial = np.abs((fx * rx + fy * ry) / np.maximum(radius, 1e-6))
        tangent = np.abs((fx * -ry + fy * rx) / np.maximum(radius, 1e-6))
        radial_ratio = float(np.mean(radial[valid]) / mean_mag)
        tangent_ratio = float(np.mean(tangent[valid]) / mean_mag)
        mean_fx = float(np.mean(fx[valid]))
        mean_fy = float(np.mean(fy[valid]))
        uniform_ratio = float(np.hypot(mean_fx, mean_fy) / mean_mag)

        rotation_ratio = max(uniform_ratio, tangent_ratio)
        translation_ratio = radial_ratio * max(0.0, 1.0 - (uniform_ratio * 0.65))
        return pixel_motion * rotation_ratio, pixel_motion * translation_ratio

    def _compute_signature(self, gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
        masked = gray.copy()
        median_value = int(np.median(masked[mask > 0])) if np.any(mask > 0) else 0
        masked[mask == 0] = median_value
        small = cv2.resize(masked, (16, 16), interpolation=cv2.INTER_AREA)
        return (small >= float(np.mean(small))).reshape(-1)

    def _compute_sharpness(self, gray: np.ndarray, mask: np.ndarray) -> float:
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        values = laplacian[mask > 0]
        if values.size == 0:
            values = laplacian.reshape(-1)
        return float(np.var(values))

    def _select_candidates(
        self,
        samples: list[MotionSample],
        segment: dict[str, float],
    ) -> list[dict[str, float]]:
        motion_samples = [sample for sample in samples if sample.motion is not None]
        if not motion_samples:
            return []

        raw_motions = np.array(
            [sample.motion for sample in motion_samples], dtype=np.float32
        )
        smoothed_motions = self._smoothed_motion_values(raw_motions)
        stable_threshold = self._stable_threshold(smoothed_motions)
        moving_threshold = self._moving_threshold(raw_motions, stable_threshold)
        stable_runs = self._extract_stable_runs(
            motion_samples,
            smoothed_motions,
            stable_threshold,
        )

        candidates: list[dict[str, float]] = []
        for run in stable_runs:
            candidate = self._candidate_from_run(
                samples=motion_samples,
                smoothed_motions=smoothed_motions,
                run=run,
                stable_threshold=stable_threshold,
                moving_threshold=moving_threshold,
            )
            if candidate is not None and candidate["confidence"] >= self.config.min_confidence:
                candidates.append(candidate)

        candidates.sort(key=lambda item: item["score"], reverse=True)
        selected = self._select_spaced_candidates(candidates)
        selected.sort(key=lambda item: item["timestamp"])
        return selected[: self.config.max_candidates_per_segment]

    def _stable_threshold(self, motions: np.ndarray) -> float:
        low = float(np.percentile(motions, self.config.stable_percentile))
        median = float(np.percentile(motions, 50))
        deviation = float(np.median(np.abs(motions - median)))
        return max(1e-6, low + (deviation * 0.5), low * 1.35)

    def _moving_threshold(self, motions: np.ndarray, stable_threshold: float) -> float:
        moving = float(np.percentile(motions, self.config.moving_percentile))
        return max(moving, stable_threshold * 1.8, 1e-6)

    def _smoothed_motion_values(self, motions: np.ndarray) -> np.ndarray:
        window = max(1, int(self.config.stable_motion_median_window))
        if window <= 1 or motions.size < 3:
            return motions.copy()
        if window % 2 == 0:
            window += 1
        if motions.size < window:
            window = motions.size if motions.size % 2 == 1 else motions.size - 1
        if window <= 1:
            return motions.copy()

        pad = window // 2
        padded = np.pad(motions, (pad, pad), mode="edge")
        return np.array(
            [
                float(np.median(padded[idx : idx + window]))
                for idx in range(motions.size)
            ],
            dtype=np.float32,
        )

    def _extract_stable_runs(
        self,
        samples: list[MotionSample],
        smoothed_motions: np.ndarray,
        stable_threshold: float,
    ) -> list[tuple[int, int]]:
        runs: list[tuple[int, int]] = []
        run_start: int | None = None

        for idx, sample in enumerate(samples):
            motion = (
                float(smoothed_motions[idx])
                if idx < smoothed_motions.size
                else sample.motion
            )
            is_stable = motion is not None and motion <= stable_threshold
            if is_stable and run_start is None:
                run_start = idx
            elif not is_stable and run_start is not None:
                self._append_stable_run(runs, samples, run_start, idx - 1)
                run_start = None

        if run_start is not None:
            self._append_stable_run(runs, samples, run_start, len(samples) - 1)

        return runs

    def _append_stable_run(
        self,
        runs: list[tuple[int, int]],
        samples: list[MotionSample],
        start_idx: int,
        end_idx: int,
    ) -> None:
        duration = samples[end_idx].time - samples[start_idx].time
        sample_period = 1.0 / max(0.1, float(self.config.fps))
        duration += sample_period
        if duration >= float(self.config.min_stable_duration):
            runs.append((start_idx, end_idx))

    def _candidate_from_run(
        self,
        samples: list[MotionSample],
        smoothed_motions: np.ndarray,
        run: tuple[int, int],
        stable_threshold: float,
        moving_threshold: float,
    ) -> dict[str, float] | None:
        start_idx, end_idx = run
        run_samples = samples[start_idx : end_idx + 1]
        run_smoothed_motions = smoothed_motions[start_idx : end_idx + 1]
        if not run_samples:
            return None

        best_offset, best_sample = max(
            enumerate(run_samples),
            key=lambda item: (
                item[1].sharpness,
                -(
                    float(run_smoothed_motions[item[0]])
                    if item[0] < run_smoothed_motions.size
                    else (item[1].motion or 0.0)
                ),
                -(item[1].motion or 0.0),
            ),
        )
        start_time = run_samples[0].time
        end_time = run_samples[-1].time + (1.0 / max(0.1, float(self.config.fps)))
        duration = max(0.0, end_time - start_time)
        pre_rotation_metrics = self._pre_motion_metrics(
            samples, start_idx, "rotation_motion", stable_threshold
        )
        post_rotation_metrics = self._post_motion_metrics(
            samples, end_idx, "rotation_motion", stable_threshold
        )
        post_translation_metrics = self._post_motion_metrics(
            samples, end_idx, "translation_motion", stable_threshold
        )
        pre_rotation = pre_rotation_metrics["score"]
        post_rotation = post_rotation_metrics["score"]
        post_translation = post_translation_metrics["score"]
        raw_mean_motion = float(
            np.mean([sample.motion or 0.0 for sample in run_samples])
        )
        smoothed_mean_motion = float(np.mean(run_smoothed_motions))
        best_smoothed_motion = (
            float(run_smoothed_motions[best_offset])
            if best_offset < run_smoothed_motions.size
            else float(best_sample.motion or 0.0)
        )
        stability = 1.0 - min(
            1.0, smoothed_mean_motion / max(stable_threshold, 1e-6)
        )
        pre_rotation_strength = min(1.0, pre_rotation / max(moving_threshold, 1e-6))
        post_rotation_strength = min(
            1.0, post_rotation / max(moving_threshold, 1e-6)
        )
        post_translation_strength = min(
            1.0, post_translation / max(moving_threshold, 1e-6)
        )
        departure_penalty = 0.0
        sharpness_score = min(1.0, best_sample.sharpness / 250.0)
        duration_score = min(1.0, duration / 1.6)
        edge_penalty = self._edge_penalty(samples, start_time, end_time)
        confidence = max(
            0.0,
            min(
                1.0,
                (stability * 0.28)
                + (pre_rotation_strength * 0.23)
                + (post_rotation_strength * 0.23)
                + (sharpness_score * 0.15)
                + (duration_score * 0.18)
                - (edge_penalty * 0.25),
            ),
        )
        score = (
            (duration_score * 2.4)
            + (pre_rotation_strength * 2.8)
            + (post_rotation_strength * 2.8)
            + (sharpness_score * 1.2)
            + stability
            - (edge_penalty * 3.0)
        )
        confidence_components = {
            "stability": stability * 0.28,
            "pre_rotation": pre_rotation_strength * 0.23,
            "post_rotation": post_rotation_strength * 0.23,
            "sharpness": sharpness_score * 0.15,
            "duration": duration_score * 0.18,
            "departure_penalty": 0.0,
            "edge_penalty": -(edge_penalty * 0.25),
        }
        score_components = {
            "duration": duration_score * 2.4,
            "pre_rotation": pre_rotation_strength * 2.8,
            "post_rotation": post_rotation_strength * 2.8,
            "sharpness": sharpness_score * 1.2,
            "stability": stability,
            "departure_penalty": 0.0,
            "edge_penalty": -(edge_penalty * 3.0),
        }

        return {
            "timestamp": float(best_sample.time),
            "timestamp_ms": float(round(best_sample.time * 1000)),
            "start_time": float(start_time),
            "end_time": float(end_time),
            "duration": float(duration),
            "motion_score": float(raw_mean_motion),
            "smoothed_motion_score": float(smoothed_mean_motion),
            "best_smoothed_motion": float(best_smoothed_motion),
            "pre_motion_score": float(pre_rotation),
            "post_motion_score": float(post_rotation),
            "pre_rotation_score": float(pre_rotation),
            "post_rotation_score": float(post_rotation),
            "post_translation_score": float(post_translation),
            "pre_rotation_peak": pre_rotation_metrics["peak"],
            "pre_rotation_energy": pre_rotation_metrics["energy"],
            "pre_rotation_energy_score": pre_rotation_metrics["energy_score"],
            "post_rotation_peak": post_rotation_metrics["peak"],
            "post_rotation_energy": post_rotation_metrics["energy"],
            "post_rotation_energy_score": post_rotation_metrics["energy_score"],
            "post_translation_peak": post_translation_metrics["peak"],
            "post_translation_energy": post_translation_metrics["energy"],
            "post_translation_energy_score": post_translation_metrics["energy_score"],
            "stability_score": float(stability),
            "pre_rotation_strength": float(pre_rotation_strength),
            "post_rotation_strength": float(post_rotation_strength),
            "post_translation_strength": float(post_translation_strength),
            "sharpness_score": float(sharpness_score),
            "duration_score": float(duration_score),
            "departure_penalty": float(departure_penalty),
            "edge_penalty": float(edge_penalty),
            "stable_threshold": float(stable_threshold),
            "moving_threshold": float(moving_threshold),
            "sharpness": float(best_sample.sharpness),
            "confidence": float(confidence),
            "score": float(score),
            "confidence_components": confidence_components,
            "score_components": score_components,
            "signature": best_sample.signature,
        }

    def _pre_motion_metrics(
        self,
        samples: list[MotionSample],
        start_idx: int,
        field_name: str,
        stable_threshold: float,
    ) -> dict[str, float]:
        start_time = samples[start_idx].time
        window_start = start_time - max(0.0, float(self.config.pre_motion_window))
        return self._motion_window_metrics(
            [
                float(getattr(sample, field_name, 0.0) or 0.0)
                for sample in samples[:start_idx]
                if window_start <= sample.time < start_time
            ],
            stable_threshold,
        )

    def _post_motion_metrics(
        self,
        samples: list[MotionSample],
        end_idx: int,
        field_name: str,
        stable_threshold: float,
    ) -> dict[str, float]:
        end_time = samples[end_idx].time
        window_end = end_time + max(0.0, float(self.config.post_motion_window))
        return self._motion_window_metrics(
            [
                float(getattr(sample, field_name, 0.0) or 0.0)
                for sample in samples[end_idx + 1 :]
                if end_time < sample.time <= window_end
            ],
            stable_threshold,
        )

    def _motion_window_metrics(
        self, values: list[float], stable_threshold: float
    ) -> dict[str, float]:
        if not values:
            return {
                "score": 0.0,
                "burst": 0.0,
                "peak": 0.0,
                "energy": 0.0,
                "energy_score": 0.0,
            }
        arr = np.array(values, dtype=np.float32)
        peak = float(np.max(arr))
        burst = self._motion_burst_score(values)
        energy_values = np.maximum(arr - float(stable_threshold), 0.0)
        energy = float(np.sum(energy_values))
        energy_score = float(energy / np.sqrt(max(1, arr.size)))
        return {
            "score": float(max(burst, peak * 0.85, energy_score)),
            "burst": float(burst),
            "peak": peak,
            "energy": energy,
            "energy_score": energy_score,
        }

    def _motion_burst_score(self, values: list[float]) -> float:
        if not values:
            return 0.0
        arr = np.array(values, dtype=np.float32)
        if arr.size <= 2:
            return float(np.max(arr))
        cutoff = float(
            np.percentile(arr, float(self.config.motion_burst_percentile))
        )
        high_values = arr[arr >= cutoff]
        if high_values.size == 0:
            return float(np.max(arr))
        return float(np.mean(high_values))

    def _edge_penalty(
        self,
        samples: list[MotionSample],
        start_time: float,
        end_time: float,
    ) -> float:
        guard = max(0.0, float(self.config.edge_guard_seconds))
        if guard <= 0 or not samples:
            return 0.0
        segment_start = samples[0].time
        segment_end = samples[-1].time
        start_gap = max(0.0, start_time - segment_start)
        end_gap = max(0.0, segment_end - end_time)
        start_penalty = max(0.0, 1.0 - (start_gap / guard))
        end_penalty = max(0.0, 1.0 - (end_gap / guard))
        return max(start_penalty, end_penalty)

    def _select_spaced_candidates(
        self, candidates: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        selected: list[dict[str, float]] = []
        min_gap = max(0.0, float(self.config.min_candidate_gap))
        for candidate in candidates:
            if len(selected) >= self.config.max_candidates_per_segment:
                break
            if all(
                abs(candidate["timestamp"] - existing["timestamp"]) >= min_gap
                for existing in selected
            ) and all(
                not self._is_duplicate_candidate(candidate, existing)
                for existing in selected
            ):
                selected.append(candidate)
        return selected

    def _is_duplicate_candidate(
        self, candidate: dict[str, float], existing: dict[str, float]
    ) -> bool:
        signature = candidate.get("signature")
        existing_signature = existing.get("signature")
        if not isinstance(signature, np.ndarray) or not isinstance(
            existing_signature, np.ndarray
        ):
            return False

        distance = self._signature_distance(signature, existing_signature)
        if distance > float(self.config.duplicate_hash_threshold):
            return False

        time_gap = abs(float(candidate["timestamp"]) - float(existing["timestamp"]))
        return (
            time_gap <= float(self.config.duplicate_time_window)
            or distance <= float(self.config.duplicate_hash_threshold) * 0.5
        )

    def _signature_distance(self, signature: np.ndarray, other: np.ndarray) -> float:
        if signature.shape != other.shape or signature.size == 0:
            return 1.0
        return float(np.mean(signature != other))
