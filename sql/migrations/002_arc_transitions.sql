-- Edge-expanded (line) graph: one row per permitted arc -> arc movement.
--
-- This exists because the Ubuntu pgRouting 3.6.1 build does not ship pgr_trsp
-- (verified with scripts/probe-pgrouting.sh), so a banned manoeuvre cannot be
-- handed to pgRouting directly. In this graph a node IS an arc, so a prohibited
-- turn is expressed by the ABSENCE of an edge: a search physically cannot make
-- a movement that has no row here.
--
-- Only consulted when a candidate route actually violates a restriction. AMDS
-- publishes 60 restricted turns nationally, so that is rare.
CREATE TABLE IF NOT EXISTS arc_transitions (
    snapshot_id     text   NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    transition_id   bigint NOT NULL,
    from_arc        bigint NOT NULL,
    to_arc          bigint NOT NULL,
    via_node        bigint NOT NULL,
    PRIMARY KEY (snapshot_id, transition_id)
);

CREATE INDEX IF NOT EXISTS arc_trans_snapshot_idx ON arc_transitions (snapshot_id);
CREATE INDEX IF NOT EXISTS arc_trans_from_idx     ON arc_transitions (snapshot_id, from_arc);
CREATE INDEX IF NOT EXISTS arc_trans_to_idx       ON arc_transitions (snapshot_id, to_arc);

-- Builds the expanded graph for one snapshot.
--
-- A movement is admitted when the incoming arc ends where the outgoing arc
-- begins, EXCEPT where a turn restriction bans that pair. Restrictions longer
-- than two links are handled at query time by the caller; a two-link
-- restriction is removed here, at the structural level, where it cannot be
-- circumvented.
CREATE OR REPLACE FUNCTION build_arc_transitions(p_snapshot text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM arc_transitions WHERE snapshot_id = p_snapshot;

    INSERT INTO arc_transitions (snapshot_id, transition_id, from_arc, to_arc, via_node)
    SELECT p_snapshot,
           row_number() OVER (ORDER BY a.arc_id, b.arc_id) - 1,
           a.arc_id, b.arc_id, a.target
    FROM arcs a
    JOIN arcs b
      ON b.snapshot_id = a.snapshot_id
     AND b.source = a.target
    WHERE a.snapshot_id = p_snapshot
      -- Never allow an immediate reversal back along the same physical link:
      -- a U-turn on the spot is not a manoeuvre the network model describes.
      AND b.link_id <> a.link_id
      -- Drop pairs banned by a two-link restriction.
      AND NOT EXISTS (
            SELECT 1 FROM turn_restrictions t
            WHERE t.snapshot_id = p_snapshot
              AND array_length(t.link_seq, 1) = 2
              AND t.link_seq[1] = a.link_id
              AND t.link_seq[2] = b.link_id
          );

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
