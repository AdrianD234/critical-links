"""Speed assignment for the time metric.

IMPORTANT: the AMDS Network Model carries NO speed attribute. Discovery of
layer 1 returned 31 fields and none is a speed limit or an observed travel
speed (see docs/SOURCE_DISCOVERY.md for the full field list). Every speed below
is an ASSUMPTION derived from asset type, surface and land-use classification,
and every link built from this table is stamped accordingly.

Consequences, which the API and UI both surface:
  - the DISTANCE metric is the defensible one;
  - time results are labelled TIME_ESTIMATED and must not be presented as
    observed or even as posted travel time;
  - enriching from the National Speed Limit Register would replace these and
    set speed_source to 'nslr'. That is the documented next step.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

#: AMDS assetOwnerOrganisation code for NZTA (the state highway network).
OWNER_NZTA = 1

SpeedSource = Literal[
    "none",
    "estimated_asset_type",
    "estimated_urban_rural",
    "nslr",
]


class Speed(NamedTuple):
    kph: float
    source: SpeedSource


def assign_speed(
    *,
    model_asset_type: int | None,
    surface_type: int | None,
    asset_owner: int | None,
    urban_rural: str | None,
) -> Speed:
    """Deliberately coarse. A fine-grained guess would imply precision the
    source does not support."""
    # Connectors are short stitching geometries at intersections and car parks.
    # Their classification dominates whatever the urban/rural table says.
    if model_asset_type == 6:
        return Speed(20.0, "estimated_asset_type")

    # Unsurfaced or metalled carriageway.
    if surface_type in (2, 3):
        return Speed(40.0, "estimated_asset_type")

    # Best available grounding: the AMDS UrbanRural table. Still an estimate -
    # it is a land-use classification, not a posted limit - but it is derived
    # from the source rather than guessed from ownership.
    if urban_rural == "urban":
        return Speed(50.0, "estimated_urban_rural")
    if urban_rural == "rural":
        return Speed(
            100.0 if asset_owner == OWNER_NZTA else 80.0, "estimated_urban_rural"
        )

    # No urban/rural coverage: fall back to ownership.
    if asset_owner == OWNER_NZTA:
        return Speed(90.0, "estimated_asset_type")
    return Speed(50.0, "estimated_asset_type")
