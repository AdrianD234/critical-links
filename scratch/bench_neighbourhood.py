"""p50/p95 for a bounded neighbourhood extraction on the national snapshot.

Measures the copy, which is the part that replaced a whole-table duplication.
Reported against the 5 s interactive ceiling.
"""
import random
import statistics
import time

from nzcl import db, neighbourhood, whatif

SNAP = "amds-national-2026-07-28-5b359d84"
N = 25

total = db.query("SELECT count(*) AS n FROM links WHERE snapshot_id=%s",
                 (SNAP,))[0]["n"]
print(f"national snapshot: {total} links")

rng = random.Random("bench-neighbourhood")
ids = [r["link_id"] for r in db.query(
    "SELECT link_id FROM links WHERE snapshot_id=%s AND mode_vehicle "
    " ORDER BY link_id", (SNAP,))]
sample = rng.sample(ids, N)

# --- how big is a neighbourhood, and how long does counting take? ---
count_ms, sizes = [], []
for lid in sample:
    t0 = time.perf_counter()
    n = neighbourhood.count_links_within(SNAP, lid, 5000.0)
    count_ms.append((time.perf_counter() - t0) * 1000)
    sizes.append(n)


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


print(f"\n5 km neighbourhood SIZE over {N} random links:")
print(f"  p50 {pct(sizes,50)} links   p95 {pct(sizes,95)} links   "
      f"max {max(sizes)}   ({100*pct(sizes,50)/total:.2f}% of national at p50)")
print(f"  over the {neighbourhood.MAX_LINKS} ceiling: "
      f"{sum(1 for s in sizes if s > neighbourhood.MAX_LINKS)} of {N}")
print(f"  count query: p50 {pct(count_ms,50):.0f} ms  p95 {pct(count_ms,95):.0f} ms")

# --- the extraction itself ---
ex_s = []
derived = []
for lid in sample:
    try:
        ex = neighbourhood.extract(SNAP, lid, radius_m=5000.0)
        derived.append((ex.transition_count, ex.component_count))
    except neighbourhood.NeighbourhoodTooSmall as e:
        print(f"  link {lid}: {e.detail}")
        continue
    ex_s.append(ex.seconds)
    whatif.drop_snapshot(ex.snapshot_id)

print(f"\n5 km EXTRACTION over {len(ex_s)} links:")
print(f"  p50 {pct(ex_s,50):.2f} s   p95 {pct(ex_s,95):.2f} s   "
      f"max {max(ex_s):.2f} s")
print(f"  against the 5 s interactive ceiling: "
      f"{'OK' if pct(ex_s,95) < 5 else 'OVER'} at p95")

# --- what a whole-table copy costs, for one link, for comparison ---
t0 = time.perf_counter()
dst = "cf-bench-fullcopy"
try:
    whatif.copy_snapshot(SNAP, dst)
    full = time.perf_counter() - t0
    print(f"\nwhole-snapshot copy (the old mechanism): {full:.1f} s for ONE "
          f"counterfactual")
    print(f"  ratio vs bounded p50: {full / max(pct(ex_s,50), 1e-9):.0f}x")
finally:
    whatif.drop_snapshot(dst)
    print(f"  national snapshot intact: "
          f"{db.query('SELECT count(*) AS n FROM links WHERE snapshot_id=%s', (SNAP,))[0]['n']} links")

if derived:
    tr = [d[0] for d in derived]
    co = [d[1] for d in derived]
    print(f"\nderived structures rebuilt per copy:")
    print(f"  arc transitions p50 {pct(tr,50)}  p95 {pct(tr,95)}")
    print(f"  components       p50 {pct(co,50)}  p95 {pct(co,95)}")
    print("  (transitions REBUILT by build_arc_transitions, components "
          "RECOMPUTED, physical access REBUILT - none copied)")
