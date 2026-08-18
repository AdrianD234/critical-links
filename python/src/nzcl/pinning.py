"""Pinning the analytical object, so a counterfactual compares like with like.

WHY FOUR SCALARS ARE NOT ENOUGH
-------------------------------
The bounded counterfactual copy validates itself by reproducing the canonical
answer with nothing assumed. The first version compared status, distance,
bridge flag and isolated-link count.

That is not sufficient, and the failure is quiet. A bounded copy can select a
DIFFERENT principal movement, or different boundary ports, and return the same
distance by coincidence - two ways out of a closure that happen to cost the
same, which is not rare on a grid. The copy then looks validated while the
counterfactual measures a different analytical object from the canonical
answer it is being compared against. Everything downstream reads as a finding.

Worse, it compounds with a copy whose derived structures were missing: a
fragment with no `arc_transitions` and national `component_id` labels answers
from a different MODEL, and four scalars are exactly the wrong instrument for
noticing that.

WHAT IS PINNED
--------------
Everything that identifies WHICH question was answered, not just what the
answer was:

    the closure          which links were removed, as a set
    the movement         its id, and its entry and exit nodes
    the ports            the boundary the replacement was routed between
    the profile, metric  a car-distance answer is not a heavy-time answer
    restriction state    whether turn restrictions were checked, and violated
    resolved state       whether the answer was resolved at all, and why not
    isolation            LENGTH as well as count - a count matches easily
    the route            the ordered arcs, hashed

THE STRONGER FORM, WHICH IS WHAT `freeze` IS FOR
------------------------------------------------
Even a full fingerprint only detects a divergence after the fact. The safer
construction is not to let the copy choose at all: FREEZE the movement chosen
on the full snapshot, and re-run THAT EXACT movement on the copy. Then the
question the copy answers is the question the canonical answer came from, by
construction rather than by comparison, and the fingerprint becomes a check on
the machinery rather than the only thing standing between a coincidence and a
published finding.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def _digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class MovementPin:
    """The exact movement to re-run, frozen from the full snapshot.

    Passed INTO the counterfactual run rather than rediscovered by it. A copy
    that is told which movement to answer cannot answer a different one.
    """

    movement_id: str
    entry_node: int
    exit_node: int
    entry_port_link: int | None = None
    exit_port_link: int | None = None

    @property
    def key(self) -> str:
        return _digest([self.movement_id, self.entry_node, self.exit_node,
                        self.entry_port_link, self.exit_port_link])


@dataclass(frozen=True)
class AnalysisPin:
    """Everything that says WHICH question this is.

    Compared in full before a copy is trusted. Every field is here because
    two analyses can differ in it while producing the same distance.
    """

    closure_links: tuple[int, ...]
    profile: str
    metric: str
    movement: MovementPin | None = None
    #: Ordered arcs of the replacement route. The strongest single signal:
    #: two routes of equal cost through different roads differ here.
    route_arcs: tuple[int, ...] = ()
    #: Was the answer resolved, and if not, why not.
    status: str = ""
    unresolved_reason: str | None = None
    #: Turn restrictions: whether they were checked at all, how many applied,
    #: and whether any were violated. A copy that silently lost its
    #: restrictions checks nothing and reports no violations, which looks
    #: identical to a clean result.
    restrictions_checked: bool = False
    restrictions_applicable: int = 0
    restriction_violations: int = 0
    #: Isolation, by LENGTH as well as by count. Counts collide easily;
    #: metres of severed road do not.
    isolated_link_count: int | None = None
    isolated_length_m: float | None = None
    is_bridge: bool | None = None
    distance_m: float | None = None

    def fingerprint(self) -> str:
        return _digest([
            sorted(self.closure_links), self.profile, self.metric,
            self.movement.key if self.movement else None,
            list(self.route_arcs), self.status, self.unresolved_reason,
            self.restrictions_checked, self.restrictions_applicable,
            self.restriction_violations,
            self.isolated_link_count,
            None if self.isolated_length_m is None
            else round(self.isolated_length_m, 3),
            self.is_bridge,
            None if self.distance_m is None else round(self.distance_m, 3),
        ])

    def differences(self, other: "AnalysisPin") -> list[str]:
        """Every field that differs, named. An empty list is agreement.

        Named rather than counted, because "the copy disagreed" is not
        actionable and "the copy chose a different principal movement" is.
        """
        out: list[str] = []

        def cmp(label, a, b, fmt=str):
            if a != b:
                out.append(f"{label}: canonical {fmt(a)} vs copy {fmt(b)}")

        cmp("closure", tuple(sorted(self.closure_links)),
            tuple(sorted(other.closure_links)))
        cmp("profile", self.profile, other.profile)
        cmp("metric", self.metric, other.metric)
        if (self.movement is None) != (other.movement is None):
            out.append(f"movement: canonical {self.movement} vs copy "
                       f"{other.movement}")
        elif self.movement is not None:
            cmp("movement id", self.movement.movement_id,
                other.movement.movement_id)
            cmp("entry node", self.movement.entry_node,
                other.movement.entry_node)
            cmp("exit node", self.movement.exit_node, other.movement.exit_node)
            cmp("entry port link", self.movement.entry_port_link,
                other.movement.entry_port_link)
            cmp("exit port link", self.movement.exit_port_link,
                other.movement.exit_port_link)
        cmp("status", self.status, other.status)
        cmp("unresolved reason", self.unresolved_reason,
            other.unresolved_reason)
        cmp("restrictions checked", self.restrictions_checked,
            other.restrictions_checked)
        cmp("applicable restrictions", self.restrictions_applicable,
            other.restrictions_applicable)
        cmp("restriction violations", self.restriction_violations,
            other.restriction_violations)
        cmp("isolated links", self.isolated_link_count,
            other.isolated_link_count)
        if self.isolated_length_m is not None \
                or other.isolated_length_m is not None:
            a = None if self.isolated_length_m is None \
                else round(self.isolated_length_m, 3)
            b = None if other.isolated_length_m is None \
                else round(other.isolated_length_m, 3)
            cmp("isolated length m", a, b)
        cmp("bridge", self.is_bridge, other.is_bridge)
        if self.distance_m is not None or other.distance_m is not None:
            a = None if self.distance_m is None else round(self.distance_m, 3)
            b = None if other.distance_m is None else round(other.distance_m, 3)
            cmp("distance m", a, b)
        if tuple(self.route_arcs) != tuple(other.route_arcs):
            out.append(
                f"route: {len(self.route_arcs)} arcs vs "
                f"{len(other.route_arcs)} arcs, different sequence - the copy "
                f"routed a different way round for the same cost")
        return out

    def agrees_with(self, other: "AnalysisPin") -> bool:
        return not self.differences(other)

    def as_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint(),
            "closureLinks": list(self.closure_links),
            "profile": self.profile,
            "metric": self.metric,
            "movementId": self.movement.movement_id if self.movement else None,
            "entryNode": self.movement.entry_node if self.movement else None,
            "exitNode": self.movement.exit_node if self.movement else None,
            "routeArcCount": len(self.route_arcs),
            "status": self.status,
            "unresolvedReason": self.unresolved_reason,
            "restrictionsChecked": self.restrictions_checked,
            "restrictionsApplicable": self.restrictions_applicable,
            "restrictionViolations": self.restriction_violations,
            "isolatedLinkCount": self.isolated_link_count,
            "isolatedLengthM": self.isolated_length_m,
            "isBridge": self.is_bridge,
            "distanceM": self.distance_m,
        }


@dataclass
class ValidationReport:
    """Why a copy was or was not trusted. Recorded, not just decided."""

    agreed: bool
    canonical: AnalysisPin
    observed: AnalysisPin
    differences: list[str] = field(default_factory=list)
    derived_inventory: dict = field(default_factory=dict)
    derived_missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "agreed": self.agreed,
            "canonicalFingerprint": self.canonical.fingerprint(),
            "copyFingerprint": self.observed.fingerprint(),
            "differences": list(self.differences),
            "derivedInventory": dict(self.derived_inventory),
            "derivedMissing": list(self.derived_missing),
            "what": ("The bounded copy is only trusted once it answers the "
                     "SAME question the canonical result came from - same "
                     "closure, movement, ports, profile, metric, restriction "
                     "state and route - not merely once it returns the same "
                     "distance. Two ways out of a closure can cost the same."),
        }
