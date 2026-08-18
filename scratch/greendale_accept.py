"""GREENDALE ACCEPTANCE, on the real national snapshot.

Link 234872 must come out topology-sensitive, canonical ~7,944 m, counter-
factual ~4,916 m, with the causal crossing Clintons Road x McLaughlins Road -
and explicitly NOT Greendale x Clintons, which is a genuine missing junction
that changes nothing when connected alone.
"""
import json
import sys
import time

from nzcl import impactv2, pinning, sensitivityrun

SNAP = "amds-national-2026-07-28-5b359d84"
LINK = 234872


def analyse_fn(snapshot_id, link_id, pinned_movement=None):
    return impactv2.analyse(snapshot_id, link_id, scope="segment",
                            metric="distance", profile="car",
                            with_geometry=False, with_corridor=False,
                            with_isolation=True)


def pin_fn(impact):
    p = impact.principal
    m = impact.principal_movement
    route = tuple(getattr(p, "arc_ids", ()) or ()) if p else ()
    impact._route_link_ids = tuple(getattr(p, "link_ids", ()) or ()) if p else ()
    iso = impact.isolation
    return pinning.AnalysisPin(
        closure_links=tuple(sorted(getattr(impact.closure, "link_ids", [LINK])
                                   or [LINK])),
        profile=impact.profile, metric=impact.metric,
        movement=pinning.MovementPin(
            movement_id=str(getattr(m, "movement_id", "") or ""),
            entry_node=int(getattr(m, "entry_node", -1) or -1),
            exit_node=int(getattr(m, "exit_node", -1) or -1))
        if m is not None else None,
        route_arcs=route,
        status=str(getattr(p, "status", "") or getattr(impact, "headline", "")),
        distance_m=getattr(p, "replacement_distance_m", None),
        is_bridge=getattr(iso, "closure_is_bridge", None),
        isolated_link_count=getattr(iso, "isolated_link_count", None),
        isolated_length_m=getattr(iso, "isolated_length_m", None),
        restrictions_checked=True,
    )


t0 = time.perf_counter()
imp = analyse_fn(SNAP, LINK)
p = pin_fn(imp)
print("canonical status", p.status, "distance", p.distance_m,
      "movement", p.movement, "arcs", len(p.route_arcs))
print("canonical analysis seconds", round(time.perf_counter() - t0, 2))

route_links = sorted({a for a in getattr(imp.principal, "link_ids", ()) or ()})
print("route link ids", len(route_links))

out = sensitivityrun.run(
    SNAP, LINK, analyse_fn=analyse_fn, pin_fn=pin_fn)
d = out.as_dict()
print(json.dumps({k: v for k, v in d.items()
                  if k not in ("counterfactuals", "candidateSearch")},
                 indent=2, default=str)[:2500])
print("\ncandidates:", d["candidateSearch"]["candidates"],
      d["candidateSearch"]["bySource"], "truncated",
      d["candidateSearch"]["truncated"])
if d.get("available"):
    for cf in d["counterfactuals"]:
        if cf["individuallyChangesAnswer"]:
            print("  CHANGES:", cf["assumedJunctions"], cf["distanceM"],
                  cf["whatChanged"])
