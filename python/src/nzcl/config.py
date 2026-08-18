"""Configuration, resolved from environment with discovered defaults.

The AMDS identifiers below are not guesses. They were found by the discovery
pipeline and are recorded with evidence in docs/SOURCE_DISCOVERY.md. They stay
overridable because NZTA may republish the service.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- database ---------------------------------------------------------
    # Development credentials. The database listens on loopback and the private
    # ranges WSL2 uses for the Windows host; it is never publicly exposed.
    database_url: str = "postgresql://nzcl:nzcl_local_dev@127.0.0.1:5432/nzcl"

    # --- AMDS source ------------------------------------------------------
    amds_item_id: str = "f955c118272b462e9ce757405890b87f"
    amds_experience_item_id: str = "c720e30739154520bc7d7c0fbfb2b6e5"
    amds_feature_service_url: str = (
        "https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer"
    )
    amds_link_layer_id: int = 1
    amds_restricted_turn_table_id: int = 9
    amds_authority_table_id: int = 2
    amds_routename_table_id: int = 13
    amds_routename_detail_table_id: int = 11
    amds_urbanrural_table_id: int = 12
    amds_restriction_table_id: int = 10

    sharing_api: str = "https://www.arcgis.com/sharing/rest"

    # --- application ------------------------------------------------------
    api_port: int = 8000
    application_base_url: str = "http://localhost:5173"
    data_dir: Path = REPO_ROOT / "data"

    # EPSG:2193. All analysis happens here.
    analysis_srid: int = 2193
    web_srid: int = 4326


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # A relative DATA_DIR (the .env default is "./data") would otherwise resolve
    # against the working directory, so running a pipeline from python/ would
    # scatter output into python/data. Anchor it to the repository instead.
    if not s.data_dir.is_absolute():
        s.data_dir = (REPO_ROOT / s.data_dir).resolve()
    return s


#: Only current, vehicle-accessible links enter the routable graph.
LINK_WHERE = "status=1 AND modeVehicle=1"

#: Bump when routing semantics change. This invalidates cached detours.
ALGORITHM = "pgr-dijkstra-arc"
ALGORITHM_VERSION = "2.0.0"

#: Bump when the GRAPH changes shape, not just when a number about it does.
#:
#: 2.1.0 - interior-to-interior crossings are classified and the AT_GRADE ones
#:         are noded. Until 2.0.0 they were refused unconditionally, which
#:         treated every flat rural crossroads in the country as a flyover.
#:         The graph is genuinely different, so every snapshot id changes and
#:         every cached detour is invalid, which is what this constant is for.
PROCESSING_VERSION = "2.1.0"

DEFAULT_ATTRIBUTION = (
    "Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, "
    "maintained by New Zealand Road Controlling Authorities, the Department of "
    "Conservation and NZTA."
)

#: Applies to every number this system returns. Repeated on API responses on
#: purpose: a figure should not be liftable without the caveats that belong to it.
LIMITATIONS = [
    "Structural analysis only. This is a shortest replacement path, NOT a traffic "
    "assignment: it does not predict how much traffic uses each alternative route, "
    "and no origin-destination demand, capacity or congestion model is involved.",
    "AMDS publishes no speed attribute. Time results are derived from estimated "
    "speeds (urban/rural classification where available, otherwise asset type and "
    "ownership) and are flagged TIME_ESTIMATED. Distance is the defensible metric.",
    "AMDS publishes only 60 restricted turns nationally. Banned-turn coverage is "
    "effectively negligible, so routes through complex intersections must not be "
    "presented as road-legal.",
    "Junctions are inferred, because AMDS does not split through roads at side roads. "
    "Where one road ends on another they are joined. Where two roads CROSS with "
    "neither ending, the crossing is classified on evidence: joined only where "
    "nothing in the source describes a structure, left separated where a mapped "
    "bridge, ramp or motorway carriageway says otherwise, and left separated but "
    "FLAGGED where the evidence settles nothing. A result that would change if a "
    "flagged crossing went the other way is reported as topology-sensitive rather "
    "than as fact.",
    "Height, weight and other physical restrictions are recorded as link quality "
    "flags but do not yet constrain routing.",
]
