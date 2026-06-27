import sys

import cv2

from sewerpipe_inspector.stop_detection.video_capture import open_analysis_video_capture


class FakeCapture:
    def __init__(self, opened: bool) -> None:
        self.opened = opened
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def release(self) -> None:
        self.released = True


def test_open_analysis_video_capture_prefers_ffmpeg_on_windows(monkeypatch) -> None:
    calls = []

    def fake_video_capture(*args):
        calls.append(args)
        return FakeCapture(True)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cv2, "VideoCapture", fake_video_capture)

    cap = open_analysis_video_capture("sample.mp4")

    assert cap.isOpened()
    assert calls == [("sample.mp4", cv2.CAP_FFMPEG)]


def test_open_analysis_video_capture_falls_back_when_ffmpeg_fails(monkeypatch) -> None:
    calls = []
    captures = [FakeCapture(False), FakeCapture(True)]

    def fake_video_capture(*args):
        calls.append(args)
        return captures[len(calls) - 1]

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cv2, "VideoCapture", fake_video_capture)

    cap = open_analysis_video_capture("sample.mp4")

    assert cap is captures[1]
    assert captures[0].released
    assert calls == [("sample.mp4", cv2.CAP_FFMPEG), ("sample.mp4",)]


def test_open_analysis_video_capture_uses_default_backend_off_windows(monkeypatch) -> None:
    calls = []

    def fake_video_capture(*args):
        calls.append(args)
        return FakeCapture(True)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cv2, "VideoCapture", fake_video_capture)

    cap = open_analysis_video_capture("sample.mp4")

    assert cap.isOpened()
    assert calls == [("sample.mp4",)]
