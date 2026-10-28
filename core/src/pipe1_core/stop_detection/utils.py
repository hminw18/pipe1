from __future__ import annotations

from typing import Iterable


def merge_close_segments(
    segments: list[dict[str, float]],
    merge_gap_threshold: float,
) -> list[dict[str, float]]:
    if not segments:
        return []

    merged: list[dict[str, float]] = [dict(segments[0])]
    for seg in segments[1:]:
        last = merged[-1]
        gap = seg["start_time"] - last["end_time"]
        if gap <= merge_gap_threshold:
            last["end_time"] = max(last["end_time"], seg["end_time"])
            last["duration"] = last["end_time"] - last["start_time"]
        else:
            merged.append(dict(seg))
    return merged


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return float(sum(vals) / len(vals))
