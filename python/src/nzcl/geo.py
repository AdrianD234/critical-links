"""Geodesy for NZTM2000 (EPSG:2193) and WGS84 (EPSG:4326).

All analytical distance work happens in EPSG:2193 metres. WGS84 is produced only
for web delivery.

This uses PROJ via pyproj rather than a hand-rolled series expansion. PROJ is
the reference implementation the rest of the geospatial world agrees with,
including the Esri service we ingest from, so the projection is one fewer thing
that needs proving. Accuracy against the source service is still asserted in
tests/test_geo.py.

Known limitation, carried into docs/KNOWN_LIMITATIONS.md: grid distance in a
Transverse Mercator projection differs from ground distance by the point scale
factor - 0.9996 on the central meridian, up to about 1.0006 at New Zealand's
east/west extremes, so a worst case near 0.06%. Detour RATIOS are essentially
unaffected because numerator and denominator carry the same distortion.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Sequence

from pyproj import Transformer

NZTM = 2193
WGS84 = 4326


@lru_cache(maxsize=4)
def _transformer(src: int, dst: int) -> Transformer:
    # always_xy keeps everything in (x, y) / (lon, lat) order, which avoids the
    # single most common reprojection bug.
    return Transformer.from_crs(f"EPSG:{src}", f"EPSG:{dst}", always_xy=True)


def nztm_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """NZTM2000 metres -> (lon, lat) degrees."""
    return _transformer(NZTM, WGS84).transform(x, y)


def lonlat_to_nztm(lon: float, lat: float) -> tuple[float, float]:
    """(lon, lat) degrees -> NZTM2000 metres."""
    return _transformer(WGS84, NZTM).transform(lon, lat)


def nztm_to_lonlat_many(
    xs: Sequence[float], ys: Sequence[float]
) -> tuple[list[float], list[float]]:
    """Vectorised reprojection. Much faster than per-point calls."""
    lon, lat = _transformer(NZTM, WGS84).transform(xs, ys)
    return list(lon), list(lat)


def polyline_length(coords: Iterable[tuple[float, float]]) -> float:
    """Planar length of a polyline in EPSG:2193.

    Euclidean arithmetic is correct here: EPSG:2193 is a projected metric CRS.
    """
    pts = list(coords)
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return total


def euclid(ax: float, ay: float, bx: float, by: float) -> float:
    return ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
