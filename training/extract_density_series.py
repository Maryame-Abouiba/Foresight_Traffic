import json
from pathlib import Path

import cv2
import pandas as pd
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
VIDEO_DIR = ROOT / "videos"
OUT_DIR = ROOT / "data" / "congestion"
WEIGHTS = ROOT / "yolov8n.pt"

VEHICLE_CLASSES = [2, 3, 5, 7]   # car, motorcycle, bus, truck (COCO)
CONF = 0.35
WINDOW_S = 15
MAX_SECONDS = None               # for example 600 to limit very long videos


def count_per_second(video, model):
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps)))
    rows, i = [], 0
    while cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                r = model.predict(frame, conf=CONF, classes=VEHICLE_CLASSES, verbose=False)[0]
                rows.append({"t_sec": round(i / fps, 2), "count": len(r.boxes)})
                if MAX_SECONDS and len(rows) >= MAX_SECONDS:
                    break
        i += 1
    cap.release()
    return pd.DataFrame(rows)


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(WEIGHTS))
    reference = {}
    videos = sorted(p for p in VIDEO_DIR.iterdir() if p.suffix.lower() in {".mp4", ".avi"})
    for video in videos:
        df = count_per_second(video, model)
        if df.empty:
            print(video.stem, "| no frame read, skipped")
            continue
        peak = int(df["count"].max())
        df["avg_density"] = df["count"].rolling(WINDOW_S, min_periods=1).mean()
        df["congestion_index"] = (df["avg_density"] / peak * 100).clip(upper=100) if peak else 0.0
        df.to_csv(OUT_DIR / f"density_{video.stem}.csv", index=False)
        mean_idx = round(float(df["congestion_index"].mean()), 1)
        reference[video.stem] = {"mean_index": mean_idx, "peak_count": peak,
                                 "duration_s": int(df["t_sec"].max())}
        print(f"{video.stem} | peak: {peak} vehicles | mean index: {mean_idx} | {len(df)} s analyzed")
    (OUT_DIR / "reference.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")