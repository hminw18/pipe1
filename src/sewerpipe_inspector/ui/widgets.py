from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider


MARKER_COLORS = {
    "소": QColor("#2e9f59"),
    "중": QColor("#d78521"),
    "대": QColor("#d14b4b"),
    "S": QColor("#2f6fb8"),
}


class TimelineSlider(QSlider):
    markerClicked = Signal(int)

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._markers: list[tuple[int, str]] = []
        self._dragging_from_groove = False

    def set_markers(self, markers: Iterable[tuple[int, str]]) -> None:
        self._markers = list(markers)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.maximum() <= 0 or not self._markers:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        left = 9
        right = self.width() - 9
        y = self.height() // 2 - 9
        width = max(1, right - left)

        for timestamp_ms, grade in self._markers:
            ratio = max(0.0, min(1.0, timestamp_ms / self.maximum()))
            x = int(left + ratio * width)
            color = MARKER_COLORS.get(grade, QColor("#777777"))
            painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine))
            painter.drawLine(QPoint(x, y), QPoint(x, y + 18))

        painter.end()

    def _value_from_position(self, x: float) -> int:
        left = 9
        right = self.width() - 9
        width = max(1, right - left)
        ratio = max(0.0, min(1.0, (x - left) / width))
        value_range = self.maximum() - self.minimum()
        return int(round(self.minimum() + (ratio * value_range)))

    def mousePressEvent(self, event) -> None:
        if self.maximum() > 0 and self._markers:
            left = 9
            right = self.width() - 9
            width = max(1, right - left)
            click_x = int(event.position().x())

            nearest_timestamp = None
            nearest_distance = 99999

            for timestamp_ms, _grade in self._markers:
                ratio = max(0.0, min(1.0, timestamp_ms / self.maximum()))
                marker_x = int(left + ratio * width)
                distance = abs(click_x - marker_x)
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_timestamp = timestamp_ms

            if nearest_timestamp is not None and nearest_distance <= 8:
                self.markerClicked.emit(nearest_timestamp)
                event.accept()
                return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.maximum() > self.minimum()
        ):
            self._dragging_from_groove = True
            self.setSliderDown(True)
            self.setValue(self._value_from_position(event.position().x()))
            self.sliderPressed.emit()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_from_groove:
            self.setValue(self._value_from_position(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_from_groove and event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_position(event.position().x()))
            self.setSliderDown(False)
            self._dragging_from_groove = False
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
