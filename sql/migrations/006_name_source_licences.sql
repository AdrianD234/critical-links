-- Whether a source's name may be SHOWN, as data rather than as discipline.
--
-- Computing a match and publishing its result are different acts. The matching
-- work below is offline analysis of publicly reachable services; putting the
-- resulting name on a road in the interface redistributes that source's
-- content, and that needs a licence.
--
-- Two of the three external sources publish an empty `copyrightText`, and the
-- NZTA street-names service describes itself as a basemap labelling layer on
-- an enterprise portal rather than an open-data product. So the default here
-- is withheld, and the display view joins this table rather than trusting
-- whoever writes the enrichment to remember.
--
-- Flipping `display_cleared` to true is a deliberate act with a recorded
-- reason. Nothing else has to change for the names to appear.

CREATE TABLE IF NOT EXISTS name_source_licences (
    source          text PRIMARY KEY,
    display_cleared boolean NOT NULL DEFAULT false,
    licence         text,
    attribution     text,
    -- What was actually checked, and where. A licence claim with no evidence
    -- is the thing this table exists to prevent.
    evidence        text,
    reviewed_at_utc timestamptz NOT NULL DEFAULT now()
);

INSERT INTO name_source_licences
    (source, display_cleared, licence, attribution, evidence)
VALUES
    ('amds_routename', true,
     'As the AMDS Network Model, already in use',
     'Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, '
     'maintained by New Zealand Road Controlling Authorities, the Department '
     'of Conservation and NZTA.',
     'The AMDS item carries licenseInfo and accessInformation, and the '
     'attribution is already shown in the application.'),

    ('linz_road_sections', true,
     'Creative Commons Attribution 4.0 International (CC BY 4.0)',
     'Contains road-name data sourced from the LINZ Data Service and licensed '
     'by Land Information New Zealand for reuse under CC BY 4.0.',
     'data.govt.nz CKAN package_search returns the dataset "NZ Addresses: '
     'Road Sections", organisation Land Information New Zealand, '
     'license_id CC-BY-4.0, url '
     'https://data.linz.govt.nz/layer/123109-nz-addresses-road-sections/ - '
     'the same layer id this project reads. Checked 7 August 2026. The WFS '
     'itself publishes empty ows:Fees and ows:AccessConstraints, so the '
     'catalogue is the evidence, not the service.'),

    ('nzta_street_names', false,
     'Unconfirmed - no licence published anywhere it appears',
     NULL,
     'Empty copyrightText on the layer; empty licenseInfo and '
     'accessInformation on the portal item '
     '(eb19b15540a844ada92dcaf5b054174e, owner GeospatialSystems). The item '
     'describes itself as "Street names for use with aerial photo base maps", '
     'i.e. a cartographic labelling service on NZTA''s enterprise portal '
     'rather than a published dataset. Absent from NZTA''s open data portal '
     'and from data.govt.nz, which catalogues 78 other NZTA datasets. '
     'Checked 7 August 2026. NZTA must confirm terms before any name or '
     'unnamed classification from this source is displayed.'),

    ('nzta_ramm_carriageway', false,
     'Unconfirmed - no licence published',
     NULL,
     'Empty copyrightText on the service, and not catalogued on data.govt.nz '
     'under this name. Used only for state-highway route, corridor and ramp '
     'context; no name from this source is ever stored as a display name, so '
     'clearance affects context only. Checked 7 August 2026.')
-- DO UPDATE, not DO NOTHING: this table records a REVIEW, and the migration is
-- where that review is written down. A re-run should restore the reviewed
-- state, not preserve whatever someone toggled in a psql session.
ON CONFLICT (source) DO UPDATE SET
    display_cleared = EXCLUDED.display_cleared,
    licence         = EXCLUDED.licence,
    attribution     = EXCLUDED.attribution,
    evidence        = EXCLUDED.evidence,
    reviewed_at_utc = now();

-- --------------------------------------------------------------- the view
-- Rebuilt to respect the clearance. An external name that is not cleared is
-- not shown and the link reads as unresolved, but `withheld_name_source`
-- records that a name IS known - so the Data Quality layer can report
-- "n links have a name we are not licensed to display", which is a different
-- and much more actionable statement than "n links have no name".
DROP VIEW IF EXISTS link_display_names;

CREATE VIEW link_display_names AS
SELECT
    l.snapshot_id,
    l.link_id,
    l.closure_group_id,
    CASE
        WHEN n.display_name IS NOT NULL THEN n.display_name
        WHEN n.external_name IS NOT NULL AND coalesce(x.display_cleared, false)
             THEN n.external_name
        -- A snapshot that has not been through a naming pass at all: the
        -- pilots, the CI fixture, anything ingested before this layer existed.
        -- Without this, adding the naming layer would silently un-name every
        -- one of them.
        WHEN n.closure_group_id IS NULL THEN l.road_name
        ELSE NULL
    END                                                AS display_name,
    CASE
        WHEN n.name_status IS NULL THEN
            CASE WHEN l.road_name IS NULL THEN 'unresolved' ELSE 'amds_named' END
        WHEN n.name_status IN ('externally_enriched', 'officially_unnamed')
             AND NOT coalesce(x.display_cleared, false) THEN 'unresolved'
        ELSE n.name_status
    END                                                AS name_status,
    CASE
        WHEN n.name_source IS NULL THEN
            CASE WHEN l.road_name IS NULL THEN NULL ELSE 'amds_routename' END
        WHEN n.name_source <> 'amds_routename'
             AND NOT coalesce(x.display_cleared, false) THEN NULL
        ELSE n.name_source
    END                                                AS name_source,
    -- Non-NULL only when a name exists but may not be displayed.
    CASE
        WHEN n.name_source IS NOT NULL
             AND n.name_source <> 'amds_routename'
             AND NOT coalesce(x.display_cleared, false)
             THEN n.name_source
        ELSE NULL
    END                                                AS withheld_name_source,
    n.source_field,
    n.native_name,
    n.native_name_key,
    -- Gated too. Where a link has no native AMDS name, its designation was
    -- read off an external source along with everything else, so publishing it
    -- while withholding the name would be publishing the same source by
    -- another route.
    CASE
        WHEN n.name_source IS NULL OR n.name_source = 'amds_routename'
             THEN n.route_designation
        WHEN coalesce(x.display_cleared, false) THEN n.route_designation
        ELSE NULL
    END                                                AS route_designation,
    n.alternates,
    coalesce(n.conflict, false)                        AS conflict,
    coalesce(n.is_ramp, false)                         AS is_ramp,
    n.external_source,
    n.match_confidence,
    l.road_number
FROM links l
LEFT JOIN link_names n
       ON n.snapshot_id = l.snapshot_id
      AND n.closure_group_id = l.closure_group_id
LEFT JOIN name_source_licences x
       ON x.source = n.name_source;
