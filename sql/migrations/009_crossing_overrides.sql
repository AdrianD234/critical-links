-- 009: evidence-backed crossing overrides.
--
-- WHY THIS TABLE EXISTS
-- ---------------------
-- The classifier does not get to create canonical graph nodes any more. It was
-- measured against a gate declared in advance - 350 cards, at most 4 failures -
-- and it failed in both directions at once: 32 of 350 AT_GRADE crossings are
-- not junctions at all, and 11 of 25 sampled DUPLICATE_GEOMETRY withdrawals are
-- junctions a reviewer can see. See docs/audits/at-grade-crossings/README.md
-- sections 13 and 14, and PIVOT.md.
--
-- So a canonical junction now exists at an interior-interior crossing ONLY
-- where a row here says so, and every row has to say WHO decided, on WHAT
-- evidence, and WHEN. Absence of contrary evidence is not evidence: that was
-- ORDINARY_CROSSROADS, and it is the rule that failed.
--
-- NOT SNAPSHOT-SCOPED, ON PURPOSE
-- -------------------------------
-- `crossings` is keyed by snapshot because it is a derived observation of one
-- ingest. An override is a human decision about a place on the ground, and it
-- must survive re-ingest, renumbering and reprocessing - otherwise every
-- refresh silently discards the review effort that is the entire point of the
-- mechanism. It is keyed on the AMDS SOURCE FEATURE ids, which are durable,
-- plus the point, and never on link_id, which is positional and reassigned at
-- load time.
--
-- THE PAIR IS UNORDERED
-- ---------------------
-- `source_a`/`source_b` order is NOT stable between runs: the ingest reads the
-- links table without an ORDER BY, so which feature of a crossing is "a" is
-- whatever the database returned that day. Matching on the ordered pair lost
-- 567 of 22,062 crossings when it was tried. The unique index below is built
-- on LEAST/GREATEST so one physical crossing cannot acquire two override rows
-- by swapping sides.

CREATE TABLE IF NOT EXISTS crossing_overrides (
    override_id     bigserial PRIMARY KEY,

    -- AMDS source feature ids. Durable across ingests; order not significant.
    source_a        text    NOT NULL,
    source_b        text    NOT NULL,

    -- Where. Matched against a detected crossing within a tolerance rather
    -- than exactly, because the detector re-derives the intersection point
    -- from re-merged geometry each ingest.
    geom_2193       geometry(Point, 2193) NOT NULL,

    -- What the reviewer decided. An override may assert separation as well as
    -- connection: "we looked, it is a bridge" is worth recording so the same
    -- crossing is not queued for review again every quarter.
    decision        text    NOT NULL
        CHECK (decision IN ('AT_GRADE', 'GRADE_SEPARATED')),

    -- WHY it may be believed. The four kinds are the only admissible ones.
    evidence_kind   text    NOT NULL
        CHECK (evidence_kind IN ('AUTHORITATIVE_SOURCE',
                                 'MANUAL_AERIAL_REVIEW',
                                 'SOURCE_DATA_CORRECTION',
                                 'PROJECT_OVERRIDE')),

    -- A dataset id, a URL, a review card code, a ticket. Free text, but it
    -- may not be blank: an override with nothing to check is an assertion.
    evidence_ref    text    NOT NULL CHECK (length(btrim(evidence_ref)) > 0),
    reviewer        text    NOT NULL CHECK (length(btrim(reviewer)) > 0),
    decided_on      date    NOT NULL,

    note            text    NOT NULL DEFAULT '',

    -- Withdrawal is a new fact, not an erasure. A retired override stops
    -- applying and stays readable, so "why is this junction here" and "why is
    -- it not here any more" both have answers.
    retired_on      date,

    CHECK (retired_on IS NULL OR retired_on >= decided_on)
);

-- One live decision per physical crossing, whichever way round the sides came
-- out this ingest. Retired rows are excluded so a crossing can be re-decided.
CREATE UNIQUE INDEX IF NOT EXISTS crossing_overrides_pair_live_idx
    ON crossing_overrides (LEAST(source_a, source_b), GREATEST(source_a, source_b),
                           round(ST_X(geom_2193)::numeric, 1),
                           round(ST_Y(geom_2193)::numeric, 1))
    WHERE retired_on IS NULL;

CREATE INDEX IF NOT EXISTS crossing_overrides_geom_idx
    ON crossing_overrides USING GIST (geom_2193);
CREATE INDEX IF NOT EXISTS crossing_overrides_source_a_idx
    ON crossing_overrides (source_a);
CREATE INDEX IF NOT EXISTS crossing_overrides_source_b_idx
    ON crossing_overrides (source_b);


-- ---------------------------------------------------------------------------
-- Candidate ranking, recorded per snapshot.
--
-- The classifier survives here and ONLY here. Its output is a score that
-- orders which unresolved crossings are worth a human's attention, which is a
-- perfectly good use of a signal that is right about nine times in ten. What
-- it may no longer do is create a node.
--
-- `crossings.noded` remains the record of what the graph actually did. This
-- table records what the review queue should look like, and is rewritten
-- whenever the ranking inputs change - so it carries no evidence and nothing
-- is lost by dropping it.
CREATE TABLE IF NOT EXISTS crossing_candidates (
    snapshot_id     text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    crossing_id     bigint  NOT NULL,

    -- Why this crossing is worth looking at, and how much. Populated by the
    -- sensitivity work: metres of replacement path removed, whether it turns
    -- a DISCONNECTED into an OK, whether it takes a link off the bridge list.
    priority        double precision NOT NULL DEFAULT 0,
    reason          text    NOT NULL DEFAULT '',

    -- The classifier's opinion, kept as a RANKING INPUT and labelled as such
    -- so nothing downstream mistakes it for a decision.
    classifier_disposition text,
    classifier_reason      text,
    classifier_confidence  text,

    reviewed        boolean NOT NULL DEFAULT false,

    PRIMARY KEY (snapshot_id, crossing_id),
    FOREIGN KEY (snapshot_id, crossing_id)
        REFERENCES crossings (snapshot_id, crossing_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS crossing_candidates_priority_idx
    ON crossing_candidates (snapshot_id, reviewed, priority DESC);


-- ---------------------------------------------------------------------------
-- `CREATE TABLE IF NOT EXISTS` does not reshape a table that already exists,
-- so a database that ran an earlier version of this migration needs the
-- columns adding explicitly. Same convention as 008: the ALTERs below and the
-- CREATE above are kept in step, so a fresh database and an existing one
-- converge on the same shape.
ALTER TABLE crossings
    ADD COLUMN IF NOT EXISTS override_id bigint
        REFERENCES crossing_overrides ON DELETE SET NULL;
ALTER TABLE crossings
    ADD COLUMN IF NOT EXISTS override_conflict boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN crossings.override_id IS
    'The evidence-backed override that decided this crossing, if any. NULL '
    'means no override matched, which means the crossing is NOT noded in the '
    'canonical graph whatever the classifier thought of it.';
COMMENT ON COLUMN crossings.override_conflict IS
    'Two or more live overrides matched this crossing and disagreed. The '
    'crossing stays disconnected and the conflict is reported: a gate with a '
    'way round it is a recommendation.';
