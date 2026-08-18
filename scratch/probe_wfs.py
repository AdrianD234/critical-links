"""List the LINZ LDS WFS feature types visible to our key, filtered by keyword.

Read-only. The API key is never printed.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ENV = Path(__file__).resolve().parents[1] / ".env"


def key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


K = key()
WFS = f"https://data.linz.govt.nz/services;key={K}/wfs"
CACHE = Path(__file__).resolve().parent / "_wfs_caps.xml"


def caps() -> str:
    if CACHE.exists() and CACHE.stat().st_size > 1000:
        return CACHE.read_text(encoding="utf-8")
    r = requests.get(WFS, params={"service": "WFS", "request": "GetCapabilities",
                                  "version": "2.0.0"}, timeout=300)
    r.raise_for_status()
    CACHE.write_text(r.text, encoding="utf-8")
    return r.text


def main() -> None:
    terms = [t.lower() for t in sys.argv[1:]] or ["bridge"]
    xml = caps()
    root = ET.fromstring(xml)
    ns = {"wfs": "http://www.opengis.net/wfs/2.0",
          "ows": "http://www.opengis.net/ows/1.1"}
    fts = root.findall(".//wfs:FeatureTypeList/wfs:FeatureType", ns)
    print(f"total feature types advertised: {len(fts)}")
    for ft in fts:
        name = (ft.findtext("wfs:Name", default="", namespaces=ns) or "")
        title = (ft.findtext("wfs:Title", default="", namespaces=ns) or "")
        abstract = (ft.findtext("wfs:Abstract", default="", namespaces=ns) or "")
        hay = f"{name} {title}".lower()
        if not any(t in hay for t in terms):
            continue
        wgs = ft.find("ows:WGS84BoundingBox", ns)
        bb = ""
        if wgs is not None:
            bb = (wgs.findtext("ows:LowerCorner", default="", namespaces=ns)
                  + " / "
                  + wgs.findtext("ows:UpperCorner", default="", namespaces=ns))
        print("-" * 74)
        print(f"  {name}   |   {title}")
        print(f"    bbox: {bb}")
        print(f"    abstract: {re.sub(r'\\s+', ' ', abstract)[:300]}")


if __name__ == "__main__":
    main()
