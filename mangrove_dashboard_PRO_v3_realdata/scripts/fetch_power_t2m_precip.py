#!/usr/bin/env python
"""Fetch REAL air temperature (T2M) and precipitation from NASA POWER for each site centroid.

- Air temperature: T2M (°C)
- Precipitation: PRECTOTCORR (mm/day)

We aggregate DAILY data to MONTHLY means for your dashboard window.

Docs: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime
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


def fetch_power_daily(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "T2M,PRECTOTCORR",
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": start,
        "end": end,
        "format": "JSON"
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    js = r.json()
    props = js.get("properties", {})
    param = props.get("parameter", {})
    dates = sorted(set(param.get("T2M", {}).keys()) | set(param.get("PRECTOTCORR", {}).keys()))
    rows=[]
    for d in dates:
        rows.append({
            "date": datetime.strptime(d, "%Y%m%d").date().isoformat(),
            "t2m_c": param.get("T2M", {}).get(d, None),
            "precip_mm": param.get("PRECTOTCORR", {}).get(d, None),
        })
    return pd.DataFrame(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20250701", help="YYYYMMDD")
    ap.add_argument("--end", default="20251231", help="YYYYMMDD")
    args = ap.parse_args()

    sites = load_site_centroids(SITES_FP)
    df_meas = pd.read_csv(MEAS_FP)
    df_meas["date"] = pd.to_datetime(df_meas["date"], errors="coerce")

    all_monthly=[]
    for sid,(lat,lon) in sites.items():
        df = fetch_power_daily(lat, lon, args.start, args.end)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        m = df.resample("MS", on="date").mean(numeric_only=True).reset_index()
        m["site_id"] = sid
        all_monthly.append(m)

    if not all_monthly:
        raise SystemExit("No POWER data downloaded.")

    df_power = pd.concat(all_monthly, ignore_index=True)
    df_power["date"] = df_power["date"].dt.date.astype(str)

    df_meas["date"] = df_meas["date"].dt.date.astype(str)
    merged = df_meas.merge(df_power[["site_id","date","t2m_c","precip_mm"]], on=["site_id","date"], how="left")

    merged.to_csv(MEAS_FP, index=False)
    print("Updated measurements.csv with t2m_c and precip_mm")

if __name__ == "__main__":
    main()
