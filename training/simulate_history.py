import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from history import init_tables, insert_history, clear_source

REFERENCE = ROOT / "data" / "congestion" / "reference.json"

# Statistic of the measured series used as the calibration level.
# Set it to the one that matches the value shown by the application
# ("last_index", "mean_index" or "max_index").
REF_STAT = "mean_index"
# ASSUMPTION: time of day of the recorded videos (unknown). Documented in the report.
REF_HOUR = 17.0
DAYS, STEP_MIN = 28, 5


def bump(h, mu, sigma):
    return np.exp(-((h - mu) ** 2) / (2 * sigma ** 2))


def profile(h, weekend):
    f = 0.10 + 0.55 * bump(h, 8.5, 1.1) + 0.70 * bump(h, 18.0, 1.5) + 0.25 * bump(h, 13.0, 1.5)
    return f * (0.6 if weekend else 1.0)


if __name__ == "__main__":
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    rng = np.random.default_rng(42)
    init_tables()
    clear_source("simulated")          # safe to run again
    end = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    n = DAYS * 24 * 60 // STEP_MIN
    times = [start + timedelta(minutes=STEP_MIN * i) for i in range(n)]
    for zone, info in reference.items():
        ref_idx = max(float(info[REF_STAT]), 5.0)
        scale = ref_idx / profile(REF_HOUR, False)
        v = np.array([scale * profile(t.hour + t.minute / 60, t.weekday() >= 5) for t in times])
        noise = np.zeros(n)
        for i in range(1, n):
            noise[i] = 0.9 * noise[i - 1] + rng.normal(0, 1.5)
        v = v + noise
        for _ in range(DAYS // 3):     # occasional 30-minute disturbances
            k = int(rng.integers(0, n - 6))
            v[k:k + 6] += rng.uniform(10, 25)
        v = np.clip(v, 0, 100)
        insert_history(zip([zone] * n, times, v), "simulated")
        print(f"{zone} ({info['name']}): {n} rows, calibration level {ref_idx} ({REF_STAT})")