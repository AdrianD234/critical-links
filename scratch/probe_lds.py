"""Search the LINZ Data Service catalogue for bridge / tunnel / road layers.

Read-only. The API key is never printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ENV = Path(__file__).resolve().parents[1] / ".env"


def key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


K = key()
API = "https://data.linz.govt.nz/services/api/v1/layers/"


def redact(s: str) -> str:
    return s.replace(K, "<KEY>")


def search(q: str, limit: int = 40) -> None:
    print("=" * 78)
    print(f"QUERY: {q!r}")
    r = requests.get(API, params={"q": q},
                     headers={"Authorization": f"key {K}"}, timeout=90)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {redact(r.text)[:400]}")
        return
    items = r.json()
    print(f"  {len(items)} results (page 1)")
    for it in items[:limit]:
        data = it.get("data") or {}
        print(f"  - id={it.get('id'):<8} type={it.get('type'):<8} "
              f"kind={it.get('kind')} geom={data.get('geometry_type')} "
              f"crs={data.get('crs')} rows={data.get('feature_count')}")
        print(f"      title: {it.get('title')}")
        gp = it.get('group') or {}
        print(f"      group: {gp.get('name')}  published: {it.get('published_at')}")


if __name__ == "__main__":
    for q in sys.argv[1:] or ["bridge"]:
        search(q)
