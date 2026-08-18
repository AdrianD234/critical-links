"""What is AMDS layer 1's t50FID actually? Range and road_cl match rate."""
from __future__ import annotations

import random
from pathlib import Path

import requests

ENV = Path(__file__).resolve().parents[1] / ".env"
BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")


def key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


K = key()
WFS = f"https://data.linz.govt.nz/services;key={K}/wfs"


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def main() -> None:
    j = get(f"{BASE}/1/query", where="t50FID > 0", returnGeometry="false",
            outStatistics='[{"statisticType":"min","onStatisticField":"t50FID","outStatisticFieldName":"lo"},'
                          '{"statisticType":"max","onStatisticField":"t50FID","outStatisticFieldName":"hi"},'
                          '{"statisticType":"count","onStatisticField":"t50FID","outStatisticFieldName":"n"}]')
    print("t50FID stats:", j["features"][0]["attributes"])

    j = get(f"{BASE}/1/query", where="t50FID > 1000000", returnGeometry="false",
            outFields="t50FID", resultRecordCount=30)
    big = [f["attributes"]["t50FID"] for f in j.get("features", [])]
    print(f"sample t50FID > 1e6: {big[:15]}")
    if big:
        cql = "t50_fid IN (" + ",".join(str(v) for v in big) + ")"
        for lyr, label in (("layer-50329", "road_cl"),
                           ("layer-50244", "bridge_cl")):
            r = requests.get(WFS, params={
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeNames": f"data.linz.govt.nz:{lyr}",
                "outputFormat": "application/json", "cql_filter": cql},
                timeout=300)
            n = len(r.json().get("features", [])) if r.status_code == 200 else r.status_code
            print(f"  {len(big)} big t50FIDs found in {label}: {n}")

    print(f"  count t50FID > 1000000: "
          f"{get(f'{BASE}/1/query', where='t50FID > 1000000', returnCountOnly='true')['count']}")
    print(f"  count t50FID <= 100000: "
          f"{get(f'{BASE}/1/query', where='t50FID <= 100000', returnCountOnly='true')['count']}")


if __name__ == "__main__":
    main()
