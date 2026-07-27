"""Reproducible source discovery for the NZTA AMDS Network Model.

    nzcl-discover

The entry point is the published Experience Builder application. That item is
NOT the data: it references a web map, which references the feature service.
This walks the chain, interrogates the service, and writes both a
machine-readable report and a human-readable summary into a dated directory.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .arcgis import fetch_json, get_count, get_layer_meta
from .config import DEFAULT_ATTRIBUTION, LINK_WHERE, get_settings

GUID = re.compile(r"[0-9a-f]{32}", re.I)


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()


def _inspect_item(item_id: str) -> dict[str, Any]:
    s = get_settings()
    try:
        meta = fetch_json(f"{s.sharing_api}/content/items/{item_id}?f=pjson",
                          label=f"item {item_id}")
    except Exception as exc:  # noqa: BLE001
        return {"id": item_id, "error": str(exc), "referencedItemIds": []}

    referenced: list[str] = []
    try:
        data = fetch_json(f"{s.sharing_api}/content/items/{item_id}/data?f=pjson",
                          label=f"item {item_id} data", retries=1)
        referenced = sorted({g for g in GUID.findall(json.dumps(data))
                             if g != item_id})
    except Exception:  # noqa: BLE001 - items without a /data payload are normal
        pass

    return {
        "id": item_id,
        "title": meta.get("title"),
        "type": meta.get("type"),
        "owner": meta.get("owner"),
        "url": meta.get("url"),
        "licenceInfo": _strip_html(meta.get("licenseInfo")),
        "accessInformation": meta.get("accessInformation"),
        "modified": (datetime.fromtimestamp(meta["modified"] / 1000,
                                            timezone.utc).isoformat()
                     if meta.get("modified") else None),
        "referencedItemIds": referenced,
    }


def _classify(fields: list[dict[str, Any]]) -> dict[str, list[str]]:
    names = [f["name"] for f in fields]

    def has(pattern: str) -> list[str]:
        rx = re.compile(pattern, re.I)
        return [n for n in names if rx.search(n)]

    return {
        "suspectedStableId": has(r"^amdsID"),
        "suspectedSourceTargetNode": has(r"(fromnode|tonode|source|target|startnode|endnode)"),
        "directionality": has(r"(oneway|direction|flow)"),
        "modeAccess": has(r"^mode"),
        "restriction": has(r"(restrict|prohibit|ban)"),
        "speed": has(r"(speed|kph|kmh|limit)"),
        "zLevel": has(r"(zlevel|z_level|grade|elevation|level)"),
        "authority": has(r"(authority|rca|owner|organisation)"),
    }


def run(out_dir: Path | None = None) -> dict[str, Any]:
    s = get_settings()
    started = datetime.now(timezone.utc)
    out_dir = out_dir or (s.data_dir / "source-metadata" / "amds"
                          / started.date().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)

    print("AMDS source discovery")
    print(f"  output: {out_dir}\n")

    # 1. walk the item graph from the published application
    visited: dict[str, dict[str, Any]] = {}
    queue = [s.amds_experience_item_id]
    while queue:
        item_id = queue.pop(0)
        if item_id in visited:
            continue
        rep = _inspect_item(item_id)
        visited[item_id] = rep
        print(f"  item {item_id}  {rep.get('type') or 'ERROR'}  "
              f"{rep.get('title') or rep.get('error', '')}")
        for ref in rep["referencedItemIds"]:
            if ref not in visited and len(visited) < 30:
                queue.append(ref)

    # 2. the feature service itself
    service = fetch_json(f"{s.amds_feature_service_url}?f=json",
                         label="feature service")
    service_item = _inspect_item(s.amds_item_id)

    layer_ids = ([l["id"] for l in service.get("layers", [])]
                 + [t["id"] for t in service.get("tables", [])])
    layers = []
    for lid in layer_ids:
        m = get_layer_meta(s.amds_feature_service_url, lid)
        try:
            count = get_count(s.amds_feature_service_url, lid, "1=1")
        except Exception:  # noqa: BLE001
            count = None
        layers.append({
            "id": m.id, "name": m.name, "type": m.type,
            "geometryType": m.geometry_type,
            "objectIdField": m.object_id_field,
            "maxRecordCount": m.max_record_count,
            "supportsPagination": m.supports_pagination,
            "featureCount": count,
            "fieldCount": len(m.fields),
            "fields": [
                {"name": f["name"],
                 "type": f["type"].replace("esriFieldType", ""),
                 "alias": f.get("alias"),
                 "codedValues": ({c["code"]: c["name"]
                                  for c in f["domain"]["codedValues"]}
                                 if (f.get("domain") or {}).get("codedValues")
                                 else None)}
                for f in m.fields
            ],
            "classification": _classify(m.fields),
        })
        print(f"  layer {str(lid).rjust(2)}  {m.name[:52].ljust(52)} {count}")

    # 3. routable-subset profiling
    profile: dict[str, Any] = {}
    for where in [
        "1=1", "status=1", LINK_WHERE,
        "status=1 AND modeVehicle=1 AND modelAssetType=1",
        "status=1 AND modeVehicle=1 AND oneway=1",
        "status=1 AND modeVehicle=1 AND oneway IS NULL",
        "status=1 AND modeVehicleHeavy=1",
        "status=1 AND modeEmergencyManagement=1",
        "status=1 AND modeFerry=1",
        "status=1 AND assetOwnerOrganisation=1",
    ]:
        try:
            profile[where] = get_count(s.amds_feature_service_url,
                                       s.amds_link_layer_id, where)
        except Exception as exc:  # noqa: BLE001
            profile[where] = f"ERROR: {exc}"

    capabilities = service.get("capabilities", "")
    report = {
        "discoveredAtUtc": started.isoformat(),
        "entryPoints": {
            "nztaPage": "https://www.nzta.govt.nz/roads-and-rail/"
                        "asset-management-data-standard/amds-network-model",
            "experienceApp": f"https://experience.arcgis.com/experience/"
                             f"{s.amds_experience_item_id}",
        },
        "itemGraph": list(visited.values()),
        "featureService": {
            "url": s.amds_feature_service_url,
            "itemId": s.amds_item_id,
            "itemTitle": service_item.get("title"),
            "owner": service_item.get("owner"),
            "licenceInfo": service_item.get("licenceInfo"),
            "accessInformation": service_item.get("accessInformation"),
            "capabilities": capabilities,
            "declaresExtract": "Extract" in capabilities,
            "maxRecordCount": service.get("maxRecordCount"),
            "copyrightText": service.get("copyrightText") or None,
        },
        "layers": layers,
        "routableProfile": profile,
        "attributionUsed": DEFAULT_ATTRIBUTION,
    }

    (out_dir / "discovery-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "feature-service.raw.json").write_text(
        json.dumps(service, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_render(report), encoding="utf-8")

    print("\nWrote discovery-report.json, feature-service.raw.json, summary.md")
    print(f"Network layer {s.amds_link_layer_id} feature count: {profile.get('1=1')}")
    print(f"Vehicle-routable current links:  {profile.get(LINK_WHERE)}")
    return report


def _render(r: dict[str, Any]) -> str:
    lines = ["# AMDS source discovery summary\n",
             f"Discovered at: {r['discoveredAtUtc']}\n", "## Item chain\n",
             "| item id | type | title |", "| --- | --- | --- |"]
    for it in r["itemGraph"]:
        lines.append(f"| `{it['id']}` | {it.get('type') or 'ERROR'} | "
                     f"{it.get('title') or it.get('error', '')} |")
    fs = r["featureService"]
    lines += ["\n## Feature service\n",
              f"- URL: {fs['url']}", f"- Item id: `{fs['itemId']}`",
              f"- Owner: {fs['owner']}",
              f"- Capabilities: `{fs['capabilities']}`",
              f"- maxRecordCount: {fs['maxRecordCount']}",
              f"- Licence info: {fs['licenceInfo'] or '(none published on item)'}",
              "\n## Layers and tables\n",
              "| id | name | geometry | features | fields |",
              "| --- | --- | --- | --- | --- |"]
    for l in r["layers"]:
        lines.append(f"| {l['id']} | {l['name']} | {l['geometryType'] or 'table'} "
                     f"| {l['featureCount']} | {l['fieldCount']} |")
    lines += ["\n## Routable subset profile\n", "| where | count |",
              "| --- | --- |"]
    for k, v in r["routableProfile"].items():
        lines.append(f"| `{k}` | {v} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Discover the AMDS source")
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args(argv)
    run(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
