-- Detour Engine V2: the undirected physical-access graph, and the shadow
-- comparison that keeps V2 honest against V1.
--
-- WHY A SECOND GRAPH
-- ------------------
-- `arcs` is a DIRECTED graph. It is the right object for "can a vehicle drive
-- from u to v", and the wrong object for "is this road still attached to the
-- network". A one-way pair whose downstream endpoint is an internal node of a
-- one-way system returns DISCONNECTED for reasons that have nothing to do with
-- a road being cut off, and V1 turned that into an isolation headline.
--
-- So connectivity is computed on Gu: ONE undirected edge per graph link that
-- the profile may traverse in AT LEAST ONE direction. Gu answers exactly one
-- question - what is still attached to what - and no driving route is ever
-- derived from it. Directed access loss stays a separate measure computed from
-- `arcs`, and the two are never allowed to produce the same sentence.
--
-- WHY IT IS PRECOMPUTED
-- ---------------------
-- Bridges, articulation points, biconnected components and connected
-- components all fall out of a single linear-time Tarjan pass. Doing that once
-- per (snapshot, profile, derivation version) and storing the result turns
-- every subsequent single-segment isolation query into an index lookup.
--
-- The DFS interval (`tin`, `tout`) is the trick that makes a bridge closure
-- exact for free. In the DFS tree of a component, removing a bridge separates
-- exactly the subtree below it. A node is on that side if and only if its
-- `tin` lies in [tin_child, tout_child]. So both resulting components are
-- recovered by a range scan rather than by a graph walk, and "exact" is a
-- statement about the algorithm rather than about a search that happened to
-- terminate inside its bound.
--
-- Idempotent, like every migration here.

-- ------------------------------------------------------------------- runs
CREATE TABLE IF NOT EXISTS physical_access_runs (
    snapshot_id         text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    profile             text NOT NULL,
    -- Bump when the derivation changes shape. Old rows stay readable and are
    -- simply never selected again, exactly like a snapshot id.
    derivation_version  text NOT NULL,
    built_at_utc        timestamptz NOT NULL DEFAULT now(),
    build_ms            integer,
    node_count          integer NOT NULL,
    link_count          integer NOT NULL,
    component_count     integer NOT NULL,
    bridge_count        integer NOT NULL,
    articulation_count  integer NOT NULL,
    bcc_count           integer NOT NULL,
    -- The component treated as the network's principal connection. Recorded
    -- here rather than inferred at query time, so a report can be traced to
    -- the choice that produced it.
    principal_component_id integer,
    principal_rule      text,
    notes               text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, profile, derivation_version)
);

-- ------------------------------------------------------------------ nodes
CREATE TABLE IF NOT EXISTS physical_access_nodes (
    snapshot_id        text NOT NULL,
    profile            text NOT NULL,
    derivation_version text NOT NULL,
    node_id            bigint NOT NULL,
    component_id       integer NOT NULL,
    is_articulation    boolean NOT NULL DEFAULT false,
    -- DFS interval within the component's spanning tree. See header.
    tin                integer NOT NULL,
    tout               integer NOT NULL,
    PRIMARY KEY (snapshot_id, profile, derivation_version, node_id)
);

CREATE INDEX IF NOT EXISTS physical_access_nodes_interval_idx
    ON physical_access_nodes
       (snapshot_id, profile, derivation_version, component_id, tin);

-- ------------------------------------------------------------------ links
-- One row per UNDIRECTED edge of Gu. A link with no permitted traversal for
-- the profile is absent, not present-and-flagged: Gu is defined as the
-- traversable subgraph, and a row that is not an edge would be a trap.
CREATE TABLE IF NOT EXISTS physical_access_links (
    snapshot_id        text NOT NULL,
    profile            text NOT NULL,
    derivation_version text NOT NULL,
    link_id            bigint NOT NULL,
    u_node             bigint NOT NULL,
    v_node             bigint NOT NULL,
    length_m           double precision NOT NULL,
    component_id       integer NOT NULL,
    -- True when removing this ONE edge disconnects its component.
    is_bridge          boolean NOT NULL DEFAULT false,
    -- Biconnected component id. Two links share one iff no single node
    -- separates them.
    bcc_id             integer NOT NULL,
    -- For a bridge only: the endpoint that is the DFS CHILD. The side that
    -- separates is exactly the DFS subtree rooted here.
    bridge_child_node  bigint,
    PRIMARY KEY (snapshot_id, profile, derivation_version, link_id)
);

CREATE INDEX IF NOT EXISTS physical_access_links_component_idx
    ON physical_access_links
       (snapshot_id, profile, derivation_version, component_id);
CREATE INDEX IF NOT EXISTS physical_access_links_bridge_idx
    ON physical_access_links
       (snapshot_id, profile, derivation_version) WHERE is_bridge;

-- ------------------------------------------------------------- components
CREATE TABLE IF NOT EXISTS physical_access_components (
    snapshot_id        text NOT NULL,
    profile            text NOT NULL,
    derivation_version text NOT NULL,
    component_id       integer NOT NULL,
    node_count         integer NOT NULL,
    link_count         integer NOT NULL,
    road_length_m      double precision NOT NULL,
    -- State-highway anchors. Used, with size, to decide which side of a cut
    -- retains the principal connection - and reported so the decision is
    -- visible rather than implied.
    state_highway_link_count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, profile, derivation_version, component_id)
);

-- ------------------------------------------------------------ V2 results
-- Keyed by the deterministic closure fingerprint, not by link id: two requests
-- that remove the same arcs under the same profile and metric ARE the same
-- computation, however they were addressed.
CREATE TABLE IF NOT EXISTS closure_analysis_v2 (
    snapshot_id         text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    closure_fingerprint text NOT NULL,
    link_id             bigint NOT NULL,
    closure_scope       text NOT NULL
                        CHECK (closure_scope IN ('segment','direction','source_feature')),
    direction           text NOT NULL,
    vehicle_profile     text NOT NULL,
    metric              text NOT NULL,
    algorithm           text NOT NULL,
    algorithm_version   text NOT NULL,
    derivation_version  text NOT NULL,
    result              jsonb NOT NULL,
    runtime_ms          integer,
    computed_at_utc     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, closure_fingerprint, algorithm_version)
);

CREATE INDEX IF NOT EXISTS closure_analysis_v2_link_idx
    ON closure_analysis_v2 (snapshot_id, link_id);

-- --------------------------------------------------------------- shadow
-- V2 does not replace V1 by being switched on. It replaces V1 by being shown,
-- case by case, to differ only where V1 was wrong. This table is where that
-- evidence accumulates.
CREATE TABLE IF NOT EXISTS closure_shadow_comparisons (
    snapshot_id        text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    link_id            bigint NOT NULL,
    closure_scope      text NOT NULL,
    vehicle_profile    text NOT NULL,
    metric             text NOT NULL,
    direction          text NOT NULL,
    v1_algorithm_version text NOT NULL,
    v2_algorithm_version text NOT NULL,

    v1_status          text,
    v2_status          text,
    -- classification | metric | closure_set | isolation | none
    difference_kinds   text[] NOT NULL DEFAULT '{}',

    v1_removed_link_count integer,
    v2_removed_link_count integer,
    v1_closure_length_m   double precision,
    v2_closure_length_m   double precision,

    v1_alternative_m   double precision,
    v2_alternative_m   double precision,
    metric_delta_m     double precision,

    v1_isolation_link_count integer,
    v2_isolation_link_count integer,
    v1_isolation_length_m   double precision,
    v2_isolation_length_m   double precision,

    v1_wording         text,
    v2_wording         text,

    v1_runtime_ms      integer,
    v2_runtime_ms      integer,

    detail             jsonb NOT NULL DEFAULT '{}'::jsonb,
    compared_at_utc    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, link_id, closure_scope, vehicle_profile, metric,
                 direction, v1_algorithm_version, v2_algorithm_version)
);

CREATE INDEX IF NOT EXISTS closure_shadow_kind_idx
    ON closure_shadow_comparisons USING GIN (difference_kinds);
