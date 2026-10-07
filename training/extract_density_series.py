"""Replay each traffic video through the application's own CongestionTracker."""
import json
import os
import sys
from pathlib import Path

import cv2
import pandas as pd
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                     # locations.py uses the relative path "videos"

from congestion import CongestionTracker
from locations import scan_video_files, get_source_by_video

OUT_DIR = ROOT / "data" / "congestion"
WEIGHTS = ROOT / "yolov8n.pt"
VEHICLE_CLASSES = [2, 3, 5, 7]     # COCO: car, motorcycle, bus, truck
CONF = 0.35                        # TO CHECK against the application's counting code


def run_video(video, model):
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps)))
    tracker = CongestionTracker()
    rows, i = [], 0
    while cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                t = i / fps
                r = model.predict(frame, conf=CONF, classes=VEHICLE_CLASSES, verbose=False)[0]
                count = len(r.boxes)
                avg, vpm, idx = tracker.update(t, count)
                rows.append({"t_sec": round(t, 2), "count": count, "avg_density": avg,
                             "vehicles_per_min": vpm, "congestion_index": idx})
        i += 1
    cap.release()
    return pd.DataFrame(rows)


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(WEIGHTS))
    reference = {}
    for path in scan_video_files():
        src = get_source_by_video(path.name)
        zone = src["id"]
        df = run_video(path, model)
        if df.empty:
            print(zone, "| no frame read, skipped")
            continue
        df.to_csv(OUT_DIR / f"density_{zone}.csv", index=False)
        reference[zone] = {
            "name": src["name"], "video": path.name,
            "last_index": float(df["congestion_index"].iloc[-1]),
            "mean_index": round(float(df["congestion_index"].mean()), 1),
            "max_index": float(df["congestion_index"].max()),
            "peak_count": int(df["count"].max()),
            "duration_s": int(df["t_sec"].max()),
        }
        r = reference[zone]
        print(f"{zone} ({r['name']}) | last: {r['last_index']} | mean: {r['mean_index']} | "
              f"max: {r['max_index']} | peak count: {r['peak_count']} | {len(df)} s analyzed")
    (OUT_DIR / "reference.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")