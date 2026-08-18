"""Phase 1 step 1: reproduce the exact user request for link 234872 and record it."""
from __future__ import annotations

import json
import sys

from nzcl import closure, impactv2

SNAP = "amds-national-2026-07-28-5b359d84"
LINK = 234872


def describe(out) -> dict:
    p = out.principal
    pm = out.principal_movement
    return {
        "headline": out.headline,
        "quality_flags": out.quality_flags,
        "scope": out.scope,
        "metric": out.metric,
        "profile": out.profile,
        "closure": {
            "removed_link_ids": out.closure.removed_link_ids,
            "removed_arc_ids": out.closure.removed_arc_ids,
            "boundary_nodes": out.closure.boundary_nodes,
            "closure_nodes": out.closure.closure_nodes,
            "selected_segment_length_m": out.closure.selected_segment_length_m,
            "total_closure_length_m": out.closure.total_closure_length_m,
            "shape": out.closure.shape,
        },
        "principal": None if p is None else {
            "movement_id": p.movement_id,
            "status": p.status,
            "resolved": p.resolved,
            "entry_port_id": p.entry_port_id,
            "exit_port_id": p.exit_port_id,
            "replacement_distance_m": getattr(p, "replacement_distance_m", None),
            "intact_distance_m": getattr(p, "intact_distance_m", None),
            "network_penalty_m": getattr(p, "network_penalty_m", None),
            "arc_count": len(p.arc_ids or []),
            "arc_ids": list(p.arc_ids or []),
        },
        "principal_movement": None if pm is None else {
            "key": pm.key,
            "entry_port_id": pm.entry_port_id,
            "exit_port_id": pm.exit_port_id,
            "intact_arc_ids": list(pm.intact_arc_ids or []),
        },
        "isolation": None if out.isolation is None else {
            "topology_confidence": out.isolation.topology_confidence,
            "topology_confidence_reason": out.isolation.topology_confidence_reason,
        },
        "stage_ms": out.stage_ms,
    }


def main() -> int:
    c = closure.resolve(SNAP, LINK)
    print("== closure.resolve (default scope) ==")
    print(f"  scope                 : {c.scope}")
    print(f"  removed_link_ids      : {c.removed_link_ids}")
    print(f"  removed_arc_ids       : {c.removed_arc_ids}")
    print(f"  removed_amds_ids      : {c.removed_amds_ids}")
    print(f"  boundary_nodes        : {c.boundary_nodes}")
    print(f"  closure_nodes         : {c.closure_nodes}")
    print(f"  shape                 : {c.shape} ({c.shape_detail})")
    print(f"  selected length m     : {c.selected_segment_length_m:.1f}")
    print(f"  total closure length m: {c.total_closure_length_m:.1f}")
    print()

    out = impactv2.analyse(SNAP, LINK, metric="distance", profile="car",
                           with_geometry=False)
    d = describe(out)
    print("== impactv2.analyse ==")
    print(json.dumps(d, indent=2, default=str))
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
