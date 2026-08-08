"""Banned manoeuvres, checked AFTER routing.

WHY A POST-ROUTE CHECK
----------------------
The multi-target searches this engine is built on - `route_many_paths` - run on
the plain arc graph, which knows nothing about turns. `routing.route` handles
restrictions by re-routing a violating path on the edge-expanded graph, but that
is a one-pair operation and the whole point of the boundary model is to route
every movement in a single edge-set load.

So the guard here is a validator rather than a constraint: every ordered arc
pair of every route the engine returns - intact, replacement AND corridor - is
checked against the restricted-turn table, and a route that uses a banned
manoeuvre is not offered as the canonical detour. It is marked unsupported,
which is an honest "this engine cannot give you a legal route here", rather
than being quietly presented as one.

WHAT THE EXPOSURE ACTUALLY IS
-----------------------------
Measured on the national snapshot, 2026-08-08:

    43 restricted-turn records in total
     1 of them restricts any modelled vehicle class
    42 have restricted_vehicle, restricted_heavy AND restricted_emergency all
       false - recorded by AMDS, but restricting nothing this engine routes

So for `profile='car'` the national exposure is ONE banned manoeuvre. That is
small enough that the check costs nothing and large enough that it is not zero,
which is exactly the case for validating rather than assuming.

It is NOT a claim that New Zealand has one banned turn. It is a claim about
what AMDS publishes, and the coverage limitation is already recorded in
`config.LIMITATIONS`: banned-turn coverage is effectively negligible, so no
route from this system may be presented as road-legal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import db
from .routing import Profile

_RESTRICTION_COLUMN = {
    "car": "restricted_vehicle",
    "heavy": "restricted_heavy",
    "emergency": "restricted_emergency",
}


@dataclass
class TurnCheck:
    """What a route does about the banned manoeuvres that apply to it."""

    #: Restricted sequences that apply to this profile at all.
    applicable_restrictions: int = 0
    #: Restricted sequences this route actually uses, as link sequences.
    violations: list[list[int]] = field(default_factory=list)
    #: Where in the LINK path each violation begins.
    violation_positions: list[int] = field(default_factory=list)
    #: The route's ordered link path, kept so a reader can find the turn.
    link_path: list[int] = field(default_factory=list)
    checked: bool = False

    @property
    def ok(self) -> bool:
        return self.checked and not self.violations

    @property
    def detail(self) -> str:
        if not self.checked:
            return "the route was not checked against the restricted-turn table"
        if not self.violations:
            return (f"no banned manoeuvre on this route "
                    f"({self.applicable_restrictions} apply to this profile)")
        return (f"this route uses {len(self.violations)} banned manoeuvre(s): "
                f"{self.violations[:3]}. It is not offered as a legal route.")


def restricted_sequences(snapshot_id: str, profile: Profile) -> list[list[int]]:
    """Link sequences this profile may not traverse in order.

    Only the ones that restrict THIS profile. A record with every mode flag
    false restricts nothing that is routed, and treating it as a ban would
    invent a constraint AMDS did not publish.
    """
    col = _RESTRICTION_COLUMN[profile]
    rows = db.query(
        f"SELECT link_seq FROM turn_restrictions "
        f" WHERE snapshot_id=%s AND {col} ORDER BY restriction_id",
        (snapshot_id,))
    return [list(r["link_seq"]) for r in rows]


def link_path(snapshot_id: str, arc_ids: Sequence[int]) -> list[int]:
    """The ordered links a route runs over, consecutive duplicates collapsed.

    Order comes from `arc_ids`, not from the query: the arcs ARE the route, and
    sorting them would destroy the very thing being checked.
    """
    ids = sorted({int(a) for a in arc_ids})
    if not ids:
        return []
    rows = db.query(
        "SELECT arc_id, link_id FROM arcs "
        " WHERE snapshot_id=%s AND arc_id = ANY(%s)", (snapshot_id, ids))
    by_arc = {int(r["arc_id"]): int(r["link_id"]) for r in rows}
    out: list[int] = []
    for a in arc_ids:
        lid = by_arc.get(int(a))
        if lid is None:
            continue
        if not out or out[-1] != lid:
            out.append(lid)
    return out


def check(snapshot_id: str, arc_ids: Sequence[int], *,
          profile: Profile = "car",
          restrictions: list[list[int]] | None = None) -> TurnCheck:
    """Does this route make a manoeuvre this profile is banned from?

    `restrictions` may be passed in so a caller checking several routes of one
    request loads the table once. It is a 43-row table nationally, so this is
    tidiness rather than a performance argument.
    """
    seqs = (restricted_sequences(snapshot_id, profile)
            if restrictions is None else restrictions)
    out = TurnCheck(applicable_restrictions=len(seqs), checked=True)
    if not arc_ids:
        return out
    out.link_path = link_path(snapshot_id, arc_ids)
    if not seqs:
        return out

    for seq in seqs:
        n = len(seq)
        if n < 2 or n > len(out.link_path):
            continue
        for i in range(len(out.link_path) - n + 1):
            if out.link_path[i:i + n] == seq:
                out.violations.append(list(seq))
                out.violation_positions.append(i)
    return out


def as_dict(c: TurnCheck) -> dict:
    return {
        "checked": c.checked,
        "ok": c.ok,
        "applicableRestrictions": c.applicable_restrictions,
        "violationCount": len(c.violations),
        "violations": c.violations,
        "violationPositions": c.violation_positions,
        "detail": c.detail,
    }
