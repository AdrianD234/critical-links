"""Identifiers that survive being ingested again.

THE PROBLEM
-----------
`link_id`, `arc_id` and `node_id` are POSITIONAL. They are handed out by the
noding pass in the order source features arrive, so re-ingesting the same AMDS
extract with the features in a different order gives the same roads different
numbers. Nothing is wrong with that until something ties.

PR 1 found a BFS-order tie-break bug and the brief for PR 2 says to assume the
class recurs. It does, in a form that looks safe: a tie broken on "the stable
id" is only stable if the id is. A hash of `arc_id` is perfectly reproducible
on one database and changes completely on the next ingest, so a corridor whose
choice fell to that tie-break can flip without one metre of road changing.

That is not hypothetical here. Shuffling the input of a nine-link fixture -
which is exactly the "shuffled-input fixture" the brief asks for - flipped the
selected corridor pair on three seeds out of eight, purely through id
reassignment.

WHAT IS ACTUALLY STABLE
-----------------------
`links.amds_id` is the AMDS source feature's own GUID. It comes from the
publisher, it is the same on every ingest, and it is the only identifier in
this schema that is not an artefact of how the data was loaded.

Nodes have no external identifier - they are inferred at junctions - so their
stable key is their POSITION, in EPSG:2193 metres rounded to a millimetre. Node
assignment works to a 10 mm tolerance, so a millimetre key is finer than the
process that created the node and cannot merge two distinct ones.

These keys are for ORDERING AND TIE-BREAKING. They are not a replacement for
the integer ids, which remain the join keys and are much cheaper.
"""

from __future__ import annotations

from typing import Sequence

from . import db

#: Rounding for a node's coordinate key, in metres. Finer than the 10 mm node
#: assignment tolerance, so two nodes cannot collide onto one key.
NODE_KEY_DP = 3


def node_keys(snapshot_id: str, node_ids: Sequence[int]) -> dict[int, str]:
    """Position-based key per node: "easting,northing" in EPSG:2193 metres."""
    ids = sorted({int(n) for n in node_ids})
    if not ids:
        return {}
    rows = db.query(
        "SELECT node_id, ST_X(geom_2193) AS x, ST_Y(geom_2193) AS y "
        "  FROM nodes WHERE snapshot_id=%s AND node_id = ANY(%s)",
        (snapshot_id, ids))
    return {
        int(r["node_id"]): f"{float(r['x']):.{NODE_KEY_DP}f},"
                           f"{float(r['y']):.{NODE_KEY_DP}f}"
        for r in rows
    }


def link_keys(snapshot_id: str, link_ids: Sequence[int]) -> dict[int, str]:
    """AMDS source feature id per link. Publisher-assigned, ingest-invariant."""
    ids = sorted({int(l) for l in link_ids})
    if not ids:
        return {}
    rows = db.query(
        "SELECT link_id, amds_id FROM links "
        " WHERE snapshot_id=%s AND link_id = ANY(%s)", (snapshot_id, ids))
    return {int(r["link_id"]): str(r["amds_id"]) for r in rows}


def arc_keys(snapshot_id: str, arc_ids: Sequence[int]) -> dict[int, str]:
    """Stable key per arc: the source feature id plus which way it runs.

    A link's two arcs share an AMDS id, so the direction is what separates
    them. Without it the forward and reverse traversals of one road would tie
    on the very key that is meant to break ties.

    One AMDS feature can be split into several graph links at inferred
    junctions, so this key is NOT unique on its own. It is combined with the
    arc's own endpoints wherever uniqueness is needed - see `port_key`.
    """
    ids = sorted({int(a) for a in arc_ids})
    if not ids:
        return {}
    rows = db.query(
        "SELECT a.arc_id, a.direction, l.amds_id "
        "  FROM arcs a JOIN links l "
        "    ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id "
        " WHERE a.snapshot_id=%s AND a.arc_id = ANY(%s)", (snapshot_id, ids))
    return {int(r["arc_id"]): f"{r['amds_id']}|{r['direction']}" for r in rows}


def port_key(arc_key: str, outside_node_key: str, closure_node_key: str) -> str:
    """A boundary crossing, named by things the publisher chose.

    The two node positions disambiguate the several graph children one AMDS
    feature can be split into, which the arc key alone cannot.
    """
    return f"{arc_key}@{outside_node_key}>{closure_node_key}"


def trail_key(node_key_: str, arc_key_list: Sequence[str]) -> str:
    """A walked route, named stably: where it ends and how it got there."""
    return node_key_ + "#" + ",".join(arc_key_list)
