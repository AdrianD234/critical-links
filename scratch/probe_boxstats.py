"""t50FID coverage inside the cached Auckland test box."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
feats = json.loads((HERE / "_amds_box.json").read_text())
road = [f for f in feats if f["attributes"].get("modelAssetType") == 1]
c = Counter(bool(f["attributes"].get("t50FID")) for f in road)
print(f"roadway links in Auckland test box: {len(road)}")
print(f"  with t50FID: {c[True]}  without: {c[False]}")
