#!/usr/bin/env python
"""Fetch REAL Sea Level Anomaly (SLA) from NOAA CoastWatch ERDDAP and merge into measurements.csv.

Dataset:
- ERDDAP dataset id: noaacwBLENDEDsshDaily
- Variable: sla (meters)

We sample the nearest 0.25° grid cell at each site centroid and compute MONTHLY means.

Access form:
https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsshDaily.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import requests
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
SITES_FP = BASE / "data" / "sites.geojson"
MEAS_FP  = BASE / "data" / "measurements.csv"

def load_site_centroids(geojson_path: Path) -> dict[str, tuple[float, float]]:
    """Return dict site_id -> (lat, lon) from sites.geojson (Point/Polygon/MultiPolygon)."""
    geo = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    out = {}
    for feat in geo["features"]:
        sid = str(feat["properties"]["site_id"])
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Point":
            lon, lat = coords
        else:
            # centroid approx from vertices (fine for coarse products)
            if gtype == "Polygon":
                pts = coords[0]
            elif gtype == "MultiPolygon":
                pts = coords[0][0]
            else:
                raise ValueError(f"Unsupported geometry type: {gtype}")
            lon = float(sum(p[0] for p in pts) / len(pts))
            lat = float(sum(p[1] for p in pts) / len(pts))
        out[sid] = (lat, lon)
    return out


ERDDAP_BASE = "https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsshDaily.json"

def nearest_025(x: float) -> float:
    return round(x * 4) / 4

def fetch_sla_point(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    lat_q = nearest_025(lat)
    lon_q = nearest_025(lon)
    query = f"sla[({start}T00:00:00Z):1:({end}T00:00:00Z)][({lat_q}):1:({lat_q})][({lon_q}):1:({lon_q})]"
    url = ERDDAP_BASE + "?" + query
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    js = r.json()
    table = js.get("table", {})
    cols = table.get("columnNames", [])
    rows = table.get("rows", [])
    if not rows:
        return pd.DataFrame(columns=["date","sla_m"])
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["time"]).dt.date.astype(str)
    df["sla_m"] = pd.to_numeric(df["sla"], errors="coerce")
    return df[["date","sla_m"]]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-07-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    sites = load_site_centroids(SITES_FP)
    df_meas = pd.read_csv(MEAS_FP)
    df_meas["date"] = pd.to_datetime(df_meas["date"], errors="coerce").dt.date.astype(str)

    all_monthly=[]
    for sid,(lat,lon) in sites.items():
        df = fetch_sla_point(lat, lon, args.start, args.end)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        m = df.resample("MS", on="date").mean(numeric_only=True).reset_index()
        m["site_id"] = sid
        all_monthly.append(m)

    if not all_monthly:
        raise SystemExit("No SLA data downloaded (check ocean proximity/endpoints).")

    df_sla = pd.concat(all_monthly, ignore_index=True)
    df_sla["date"] = pd.to_datetime(df_sla["date"]).dt.date.astype(str)

    merged = df_meas.merge(df_sla[["site_id","date","sla_m"]], on=["site_id","date"], how="left")
    merged.to_csv(MEAS_FP, index=False)
    print("Updated measurements.csv with sla_m")

if __name__ == "__main__":
    main()
