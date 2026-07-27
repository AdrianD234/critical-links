"""Minimal ArcGIS Feature Service client, built for verifiable bulk extraction.

Strategy:
  1. read service and layer metadata
  2. ask for the full OBJECTID list (returnIdsOnly=true)
  3. download in bounded OBJECTID batches, respecting maxRecordCount
  4. retry with exponential backoff
  5. reconcile what came back against what was asked for

Batching by explicit id list rather than resultOffset paging is deliberate.
Offset paging over a service being edited underneath can silently skip or
duplicate rows; an id list pins exactly which features are expected, so a
shortfall is detectable rather than invisible.

Every query is POSTed. A batch of 2000 OBJECTIDs in a query string exceeds the
URL length limit and the service returns HTTP 414.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import requests


class ArcGisError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None,
                 esri_code: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.esri_code = esri_code


_session = requests.Session()


def fetch_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    retries: int = 5,
    timeout: float = 120.0,
    label: str | None = None,
) -> dict[str, Any]:
    """GET or POST (when `data` is given), with retry and Esri error handling."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(30.0, 1.0 * 2 ** (attempt - 1)) + random.random() * 0.25)
        try:
            if data is None:
                resp = _session.get(url, timeout=timeout)
            else:
                resp = _session.post(url, data=data, timeout=timeout)
            if resp.status_code != 200:
                raise ArcGisError(
                    f"HTTP {resp.status_code} for {label or url}",
                    http_status=resp.status_code,
                )
            body = resp.json()
            # ArcGIS returns HTTP 200 with an error envelope. Never treat that
            # as data.
            if isinstance(body, dict) and "error" in body:
                err = body["error"]
                details = "; ".join(err.get("details") or [])
                raise ArcGisError(
                    f"ArcGIS error {err.get('code')}: {err.get('message')}"
                    + (f" ({details})" if details else ""),
                    esri_code=err.get("code"),
                )
            return body
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
    raise last if last else ArcGisError("unreachable")


@dataclass
class LayerMeta:
    id: int
    name: str
    type: str
    geometry_type: str | None
    object_id_field: str
    max_record_count: int
    supports_pagination: bool
    fields: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def get_layer_meta(service_url: str, layer_id: int) -> LayerMeta:
    raw = fetch_json(f"{service_url}/{layer_id}?f=json", label=f"layer {layer_id} meta")
    return LayerMeta(
        id=raw.get("id", layer_id),
        name=raw.get("name", ""),
        type=raw.get("type", ""),
        geometry_type=raw.get("geometryType"),
        object_id_field=raw.get("objectIdField") or "OBJECTID",
        max_record_count=raw.get("maxRecordCount") or 1000,
        supports_pagination=bool(
            (raw.get("advancedQueryCapabilities") or {}).get("supportsPagination")
        ),
        fields=raw.get("fields") or [],
        raw=raw,
    )


def _extent_params(extent: dict[str, float] | None, wkid: int = 2193) -> dict[str, str]:
    if not extent:
        return {}
    return {
        "geometry": json.dumps(
            {
                "xmin": extent["xmin"], "ymin": extent["ymin"],
                "xmax": extent["xmax"], "ymax": extent["ymax"],
                "spatialReference": {"wkid": wkid},
            }
        ),
        "geometryType": "esriGeometryEnvelope",
        "inSR": str(wkid),
        "spatialRel": "esriSpatialRelIntersects",
    }


def get_count(service_url: str, layer_id: int, where: str,
              extent: dict[str, float] | None = None) -> int:
    body = fetch_json(
        f"{service_url}/{layer_id}/query",
        data={"where": where, "returnCountOnly": "true", "f": "json",
              **_extent_params(extent)},
        label=f"layer {layer_id} count",
    )
    return int(body.get("count", 0))


def get_object_ids(service_url: str, layer_id: int, where: str,
                   extent: dict[str, float] | None = None) -> list[int]:
    body = fetch_json(
        f"{service_url}/{layer_id}/query",
        data={"where": where, "returnIdsOnly": "true", "f": "json",
              **_extent_params(extent)},
        label=f"layer {layer_id} object ids",
    )
    return list(body.get("objectIds") or [])


@dataclass
class DownloadResult:
    features: list[dict[str, Any]]
    sha256: str
    batches: int
    missing_ids: list[int]
    duplicate_ids: list[int]


def download_by_ids(
    service_url: str,
    layer_id: int,
    object_ids: Sequence[int],
    *,
    out_fields: Iterable[str],
    return_geometry: bool,
    out_sr: int,
    batch_size: int,
    object_id_field: str = "OBJECTID",
    concurrency: int = 6,
    on_progress: Callable[[int, int], None] | None = None,
) -> DownloadResult:
    """Download features by explicit OBJECTID batches, then reconcile.

    A shortfall is reported through `missing_ids` so the caller can mark the
    snapshot partial rather than silently shipping a hole in the network.
    """
    fields = list(out_fields)
    # Reconciliation reads the OBJECTID off each returned feature, so the id
    # field must be requested even when the caller wants only a few columns.
    if object_id_field not in fields and "*" not in fields:
        fields.insert(0, object_id_field)

    ids = list(object_ids)
    batches = [ids[i:i + batch_size] for i in range(0, len(ids), max(1, batch_size))]
    pages: list[list[dict[str, Any]] | None] = [None] * len(batches)
    hashes: list[str] = [""] * len(batches)
    done = 0

    def work(idx: int) -> None:
        nonlocal done
        body = fetch_json(
            f"{service_url}/{layer_id}/query",
            data={
                "objectIds": ",".join(str(i) for i in batches[idx]),
                "outFields": ",".join(fields),
                "returnGeometry": "true" if return_geometry else "false",
                "outSR": str(out_sr),
                "f": "json",
            },
            label=f"batch {idx + 1}/{len(batches)}",
        )
        feats = body.get("features") or []
        pages[idx] = feats
        hashes[idx] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()
        done += len(feats)
        if on_progress:
            on_progress(done, len(ids))

    if batches:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            list(pool.map(work, range(len(batches))))

    features: list[dict[str, Any]] = []
    for page in pages:
        if page:
            features.extend(page)

    seen: dict[int, int] = {}
    for f in features:
        oid = (f.get("attributes") or {}).get(object_id_field)
        seen[oid] = seen.get(oid, 0) + 1
    missing = [i for i in ids if i not in seen]
    duplicates = [i for i, c in seen.items() if c > 1]

    roll = hashlib.sha256()
    for h in hashes:
        roll.update(h.encode())

    return DownloadResult(
        features=features,
        sha256=roll.hexdigest(),
        batches=len(batches),
        missing_ids=missing,
        duplicate_ids=duplicates,
    )
