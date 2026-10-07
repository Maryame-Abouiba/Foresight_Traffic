"""Export the congestion values the application itself uses (frame_stats means)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from locations import get_active_zones
from routing import load_zone_congestion

OUT = ROOT / "data" / "congestion"

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    congestion = load_zone_congestion()
    reference = {}
    for zone in get_active_zones():
        val = round(float(congestion.get(zone["id"], 0.0)), 1)
        reference[zone["id"]] = {"name": zone["name"], "video": zone.get("video_file"),
                                 "mean_index": val}
        print(f"{zone['id']} ({zone['name']}) | application index: {val}")
    (OUT / "reference.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")