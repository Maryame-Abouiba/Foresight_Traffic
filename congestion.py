"""
Congestion index from vehicle density over video time.

Uses a rolling average of vehicles visible in frame, normalized against
the busiest moment observed in the same video.
"""

from __future__ import annotations

from collections import deque

# Rolling window for density averaging (seconds of video time)
DENSITY_WINDOW_SEC = 15.0
# Floor so very quiet feeds still get a meaningful scale
MIN_CAPACITY = 6.0


class CongestionTracker:
    """Compute congestion from average vehicle count over video time."""

    def __init__(self, window_sec: float = DENSITY_WINDOW_SEC):
        self.window_sec = window_sec
        self._samples: deque[tuple[float, int]] = deque()
        self._peak_density = 0.0

    def update(self, video_time_sec: float, vehicle_count: int) -> tuple[float, float, float]:
        self._samples.append((video_time_sec, vehicle_count))
        cutoff = max(0.0, video_time_sec - self.window_sec)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        counts = [c for _, c in self._samples]
        avg_density = sum(counts) / len(counts) if counts else float(vehicle_count)

        self._peak_density = max(self._peak_density, float(vehicle_count), avg_density)
        capacity = max(self._peak_density, MIN_CAPACITY)

        # Vehicles per minute of video: average presence rate over the window
        elapsed = max(video_time_sec, self.window_sec * 0.25, 0.1)
        window_span = min(self.window_sec, elapsed)
        vehicles_per_minute = avg_density * (60.0 / window_span)

        congestion = min(100.0, (avg_density / capacity) * 100.0)

        return round(avg_density, 1), round(vehicles_per_minute, 1), round(congestion, 1)


def compute_congestion_index(avg_density: float, peak_density: float) -> float:
    capacity = max(peak_density, MIN_CAPACITY)
    return round(min(100.0, (avg_density / capacity) * 100.0), 1)


def congestion_level_label(value: float) -> str:
    if value <= 0:
        return "No data"
    if value < 30:
        return "Low"
    if value < 60:
        return "Moderate"
    if value < 80:
        return "High"
    return "Severe"


def congestion_color_hex(value: float) -> str:
    if value <= 0:
        return "#64748b"
    if value < 30:
        return "#16a34a"
    if value < 60:
        return "#d97706"
    if value < 80:
        return "#dc2626"
    return "#7f1d1d"
