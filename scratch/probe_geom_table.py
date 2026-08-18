"""Profile AMDS layer 4 (Geometry) and layer 10 (Restriction) for structure signal.

Read-only.
"""
from __future__ import annotations

import requests

BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def count(lid: int, where: str) -> int:
    return get(f"{BASE}/{lid}/query", where=where,
               returnCountOnly="true").get("count", -1)


def groupby(lid: int, field: str, where: str = "1=1") -> None:
    j = get(f"{BASE}/{lid}/query", where=where,
            groupByFieldsForStatistics=field,
            outStatistics=('[{"statisticType":"count","onStatisticField":'
                           f'"{field}","outStatisticFieldName":"n"}}]'),
            returnGeometry="false")
    rows = [f["attributes"] for f in j.get("features", [])]
    rows.sort(key=lambda r: -(r.get("n") or 0))
    print(f"  group by {field}:")
    for r in rows[:25]:
        print(f"    {r.get(field)!r:>14} -> {r.get('n')}")


def main() -> None:
    print("### LAYER 10 Restriction")
    print(f"  total rows                    {count(10, '1=1')}")
    print(f"  heightRestriction=1           {count(10, 'heightRestriction=1')}")
    print(f"  heightRestriction=1, current  "
          f"{count(10, 'heightRestriction=1 AND status=1')}")
    w = "amdsIDNetworkModel IS NOT NULL AND amdsIDNetworkModel<>''"
    print(f"  amdsIDNetworkModel not null   {count(10, w)}")
    groupby(10, "heightRestriction")
    j = get(f"{BASE}/10/query", where="heightRestriction=1", outFields="*",
            resultRecordCount=8, returnGeometry="false")
    print("  sample heightRestriction=1 rows:")
    for f in j.get("features", []):
        a = f["attributes"]
        print(f"    net={a.get('amdsIDNetworkModel')} h={a.get('heightInfo')} "
              f"mode={a.get('modeRestriction')} full={a.get('isFullLength')} "
              f"org={a.get('dataManagingOrganisation')} st={a.get('status')} "
              f"info={a.get('modeInfo')!r}")

    print()
    print("### LAYER 4 Geometry")
    print(f"  total rows                    {count(4, '1=1')}")
    print(f"  zStart IS NOT NULL            {count(4, 'zStart IS NOT NULL')}")
    print(f"  zEnd IS NOT NULL              {count(4, 'zEnd IS NOT NULL')}")
    print(f"  zStart <> 0                   {count(4, 'zStart <> 0')}")
    print(f"  length3D IS NOT NULL          {count(4, 'length3D IS NOT NULL')}")
    print(f"  length3D <> measuredLength    "
          f"{count(4, 'length3D <> measuredLength')}")
    groupby(4, "zAccuracy")
    groupby(4, "zAccuracyMethodUsed")
    groupby(4, "xYAccuracyMethodUsed")
    groupby(4, "zCoordinateSystem")


if __name__ == "__main__":
    main()
