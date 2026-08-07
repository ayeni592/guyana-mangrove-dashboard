#!/usr/bin/env python
"""Fetch SMAP Sea Surface Salinity (SSS) from NOAA CoastWatch ERDDAP and merge into measurements.csv.

Dataset:
  https://coastwatch.noaa.gov/erddap/griddap/noaacwSMAPsssDaily.html
Variable:
  sss (PSU)

Usage:
  python scripts/fetch_smap_salinity_erddap.py
  python scripts/fetch_smap_salinity_erddap.py --window 1
"""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SITES_FP = DATA_DIR / "sites.geojson"
MEAS_FP  = DATA_DIR / "measurements.csv"

ERDDAP_BASE = "https://coastwatch.noaa.gov/erddap/griddap/noaacwSMAPsssDaily.json"

def load_sites() -> dict[str, tuple[float,float]]:
    """Return site_id -> (lat, lon)"""
    geo = json.loads(SITES_FP.read_text())
    out = {}
    for feat in geo["features"]:
        sid = str(feat["properties"]["site_id"])
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        # Point
        if gtype == "Point":
            lon, lat = coords
        else:
            # Polygon/MultiPolygon -> approximate centroid from vertices (simple & dependency-free)
            # This is sufficient for 0.25° SMAP SSS sampling.
            pts = []
            if gtype == "Polygon":
                rings = coords
                pts = rings[0]
            elif gtype == "MultiPolygon":
                pts = coords[0][0]
            else:
                raise ValueError(f"Unsupported geometry type: {gtype}")
            lons = [p[0] for p in pts]
            lats = [p[1] for p in pts]
            lon = float(sum(lons) / len(lons))
            lat = float(sum(lats) / len(lats))
        out[sid] = (float(lat), float(lon))
    return out

def round_to_quarter_degree(x: float) -> float:
    return round(x * 4) / 4.0

def build_query(date: pd.Timestamp, lat: float, lon: float, window: int) -> str:
    # ERDDAP dataset grid is typically (time, latitude, longitude). We'll sample a small lat/lon window.
    # Use 12:00Z for daily products.
    t = pd.Timestamp(date.date()).tz_localize("UTC") + pd.Timedelta(hours=12)
    iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")

    lat0 = round_to_quarter_degree(lat)
    lon0 = round_to_quarter_degree(lon)

    # window=0 -> exact cell; window=1 -> +/-0.25; window=2 -> +/-0.5, etc.
    step = 0.25
    lat_min = lat0 - window*step
    lat_max = lat0 + window*step
    lon_min = lon0 - window*step
    lon_max = lon0 + window*step

    # ERDDAP slice notation: [time][lat_min:lat_max][lon_min:lon_max]
    # We'll request the grid and then compute mean ignoring NaN.
    q = f"sss[{iso}][({lat_min}):({lat_max})][({lon_min}):({lon_max})]"
    return f"{ERDDAP_BASE}?{q}"

def fetch_salinity(date: pd.Timestamp, lat: float, lon: float, window: int, timeout: int = 30) -> float|None:
    url = build_query(date, lat, lon, window)
    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        return None
    js = r.json()
    # ERDDAP JSON is a table-like structure; the data values are in ["table"]["rows"] or grid-like arrays depending on endpoint
    # For griddap .json, the structure is usually ["table"]["rows"] with columns including the value.
    # We'll search for numeric values in rows and compute mean.
    table = js.get("table", {})
    rows = table.get("rows", [])
    vals = []
    for row in rows:
        # Typically: [time, latitude, longitude, sss]
        if len(row) >= 4:
            v = row[-1]
            try:
                v = float(v)
                if np.isfinite(v):
                    vals.append(v)
            except Exception:
                pass
    if not vals:
        return None
    return float(np.nanmean(vals))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=1, help="Neighborhood window in 0.25° steps (default 1 => 3x3). Use 0 for single cell.")
    ap.add_argument("--dry-run", action="store_true", help="Do not write to disk.")
    args = ap.parse_args()

    if not SITES_FP.exists() or not MEAS_FP.exists():
        raise SystemExit("Missing data/sites.geojson or data/measurements.csv")

    sites = load_sites()
    df = pd.read_csv(MEAS_FP)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    if "salinity_psu" not in df.columns:
        df["salinity_psu"] = np.nan
    if "salinity_source" not in df.columns:
        df["salinity_source"] = ""
    if "salinity_is_satellite" not in df.columns:
        df["salinity_is_satellite"] = False

    # Fetch per row (simple, robust). For larger datasets, you can cache by (date, lat_cell, lon_cell).
    updated = 0
    for i, row in df.iterrows():
        sid = str(row["site_id"])
        if sid not in sites:
            continue
        lat, lon = sites[sid]
        d = row["date"]
        # Only fill missing salinity
        if pd.isna(row["salinity_psu"]):
            val = fetch_salinity(d, lat, lon, window=args.window)
            if val is None:
                continue
            df.at[i, "salinity_psu"] = val
            df.at[i, "salinity_source"] = "SMAP SSS (NOAA CoastWatch ERDDAP: noaacwSMAPsssDaily)"
            df.at[i, "salinity_is_satellite"] = True
            updated += 1

    if args.dry_run:
        print(f"Dry run complete. Would update {updated} rows.")
        return

    df_out = df.copy()
    df_out["date"] = df_out["date"].dt.date.astype(str)
    df_out.to_csv(MEAS_FP, index=False)
    print(f"Done. Updated {updated} rows in {MEAS_FP}")

if __name__ == "__main__":
    main()
