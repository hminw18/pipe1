from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StopDetectionConfig:
    fps: float = 5.0
    min_stop_duration: float = 3.0
    merge_gap_threshold: float = 3.0
    use_optical_flow: bool = False
    resize_width: int = 640
    baseline_seconds: float = 10.0
    max_stop_duration: float | None = 60.0
    smoothing_alpha: float = 0.8
    stop_threshold_factor: float = 0.2
    move_threshold_factor: float = 0.6
    stop_enter_frames: int = 5
    stop_release_frames: int = 5
