"""One allocator for every identifier that exists only inside a request.

WHY THIS IS CENTRAL RATHER THAN LOCAL
-------------------------------------
The first version of the virtual split numbered its pieces from -1 downwards,
on the reasoning that real ids are all non-negative so anything negative is
free. That reasoning was correct and the result was still wrong, because
pgRouting marks the final row of every path with `edge = -1` and the router
filters that marker out before summing a route. The arc numbered -1 was
therefore deleted from its own path: the search succeeded, the arc list came
back one leg short, and an 1100 m replacement path measured 1000 m with nothing
reporting a problem.

The lesson is not "skip -1". It is that an ad-hoc numbering scheme carries
assumptions nobody wrote down, so the reservations live here, once, and every
request-local id in the system comes from this module.

THE LAYOUT
----------
Three disjoint bands, a trillion ids apart:

    arcs    -1_000_000_000_000  and downwards
    links   -2_000_000_000_000  and downwards
    nodes   -3_000_000_000_000  and downwards

Disjoint bands matter because several of these travel through code that types
them all as `int`. A node id and an arc id that could collide would be a defect
nothing catches: both are plausible, both index something, and the wrong lookup
returns a real answer to the wrong question. Separating the bands makes that
collision impossible rather than unlikely, and `kind_of` can name what any id
is when a message needs to say so.

The bands also put every virtual id at least a trillion away from 0 and -1, so
the two reserved values are excluded by construction and not by a `+ 1` that a
later edit could quietly undo.

Capacity is a trillion ids per band per request against a national network of
375,485 links. The ceiling is not reachable; it is asserted anyway, because a
runaway loop should fail loudly rather than wrap into another band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Reserved and never issued to anything.
#:
#: -1 is pgRouting's terminal-edge marker (see the module docstring). 0 is
#: excluded because it is the lowest REAL id in every table here, and because
#: it is what an uninitialised integer looks like - an id that is both a valid
#: real node and a plausible accident is not one worth issuing.
RESERVED_IDS = frozenset({0, -1})

#: Width of each band. Wide enough that no request approaches it, narrow enough
#: that three of them stay far inside signed BIGINT.
BAND_WIDTH = 1_000_000_000_000

#: Lowest value signed BIGINT can hold. Every id this module issues is checked
#: against it, so a bug here fails rather than silently wrapping in Postgres.
BIGINT_MIN = -(2 ** 63)

Kind = Literal["arc", "link", "node"]

#: Band index per kind. Multiplied by BAND_WIDTH to get the band's top.
_BANDS: dict[Kind, int] = {"arc": 1, "link": 2, "node": 3}


def band_top(kind: Kind) -> int:
    """The first id issued in `kind`'s band. Numbering runs downwards."""
    return -_BANDS[kind] * BAND_WIDTH


def band_floor(kind: Kind) -> int:
    """One past the last id in `kind`'s band."""
    return band_top(kind) - BAND_WIDTH


def is_virtual(identifier: int) -> bool:
    """True for anything this module could have issued."""
    return identifier <= -BAND_WIDTH


def is_real(identifier: int) -> bool:
    """True for an id that could have come from the database.

    Real link, arc and node ids are non-negative in every snapshot - verified
    against the national one, whose minima are 0.
    """
    return identifier >= 0


def kind_of(identifier: int) -> Kind | None:
    """Which band an id belongs to, or None if it is not a virtual id."""
    for kind in _BANDS:
        if band_floor(kind) < identifier <= band_top(kind):
            return kind
    return None


def describe(identifier: int) -> str:
    """A human-readable account of an id, for error messages."""
    if is_real(identifier):
        return f"{identifier} (real)"
    kind = kind_of(identifier)
    if kind is None:
        return f"{identifier} (neither a real id nor in any virtual band)"
    return f"{identifier} (virtual {kind})"


@dataclass
class VirtualIds:
    """Sequential allocation within each band, for the life of one request.

    Deterministic by construction: allocation depends only on the ORDER of
    calls, so a caller that walks its inputs in a sorted order produces the
    same ids on every run and on every machine. That is what lets a fingerprint
    over the resulting graph mean anything, and `vsplit` sorts for exactly this
    reason.

    Not thread-safe, and deliberately not shared. One request, one allocator.
    """

    _issued: dict[Kind, int] = field(default_factory=lambda: {k: 0 for k in _BANDS})

    def _next(self, kind: Kind) -> int:
        n = self._issued[kind]
        if n >= BAND_WIDTH:
            raise OverflowError(
                f"exhausted the {kind} band after {n} ids in one request; "
                f"this is a runaway allocation, not a large network")
        identifier = band_top(kind) - n
        self._issued[kind] = n + 1
        if identifier < BIGINT_MIN:  # pragma: no cover - unreachable by arithmetic
            raise OverflowError(f"{identifier} is below signed BIGINT")
        assert identifier not in RESERVED_IDS  # by construction; see module doc
        return identifier

    def arc(self) -> int:
        return self._next("arc")

    def link(self) -> int:
        return self._next("link")

    def node(self) -> int:
        return self._next("node")

    def issued(self, kind: Kind) -> int:
        """How many ids of `kind` have been handed out."""
        return self._issued[kind]

    @property
    def total_issued(self) -> int:
        return sum(self._issued.values())
