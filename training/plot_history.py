import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from history import DB_PATH

con = sqlite3.connect(DB_PATH)
df = pd.read_sql("SELECT zone, ts, congestion_index FROM congestion_history WHERE source='simulated'",
                 con, parse_dates=["ts"])
con.close()
print(df.groupby("zone")["congestion_index"].agg(["count", "min", "mean", "max"]).round(1))

last = df[df.ts >= df.ts.max() - pd.Timedelta(days=3)]
plt.figure(figsize=(10, 4))
for zone, g in last.groupby("zone"):
    plt.plot(g.ts, g.congestion_index, label=zone)
plt.ylabel("Congestion index (0 to 100)")
plt.title("Simulated congestion history, last 3 days")
plt.legend()
plt.tight_layout()
(ROOT / "docs").mkdir(exist_ok=True)
plt.savefig(ROOT / "docs" / "history_3days.png", dpi=150)