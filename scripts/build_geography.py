"""Rebuilds the committed geography from the official files and the public routing server.

    python scripts/build_geography.py            # township table only
    python scripts/build_geography.py --roads    # also the road matrix: about 90 seconds of polite requests

The road matrix is a saved snapshot. There is no need to rebuild it to reproduce the analysis.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from network_design import geography as g  # noqa: E402

if "--no-download" not in sys.argv:
    month = g.download_raw()
    print(f"Population month (ROC): {month}")
table, villages = g.build_townships()
g.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
target = g.PROCESSED_DIR / "townships.csv"
if "--roads" in sys.argv:
    table = g.refine_points(table, villages)
elif target.exists():
    # the moved points and snap_m come from the routing server, so a rebuild without --roads must keep them
    previous = pd.read_csv(target, dtype={"town_code": str})
    extra = [c for c in ("snap_m", "point_moved") if c in previous]
    table = table.drop(columns=["lon", "lat"]).merge(previous[["town_code", "lon", "lat", *extra]], on="town_code", how="left")
print(f"{len(table)} mainland townships, population {table['population'].sum():,}")
outlines = g.township_outlines(table)
(g.PROCESSED_DIR / "township_outlines.json").write_text(json.dumps(outlines, separators=(",", ":")), encoding="utf-8")
print(f"Outlines: {sum(len(ring) for rings in outlines.values() for ring in rings):,} points")

if "--roads" in sys.argv:
    # Everything is written together, after the last request, so a dropped connection cannot leave the table and the
    # matrix describing different points.
    distance, duration, snapped = g.road_matrix(table)
    codes = table["town_code"].tolist()
    g.save_matrix(g.PROCESSED_DIR / "road_km.csv.gz", distance, codes)
    g.save_matrix(g.PROCESSED_DIR / "road_hours.csv.gz", duration, codes)
    table = table.assign(snap_m=snapped.round(0))
    print(f"Road matrix saved. Largest snap to a road: {snapped.max():,.0f} m")
table.to_csv(target, index=False)
