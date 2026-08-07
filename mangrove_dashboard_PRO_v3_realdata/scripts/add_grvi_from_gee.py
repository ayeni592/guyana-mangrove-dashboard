"""
Add GRVI (Green-Red Vegetation Index) to data/measurements.csv using Google Earth Engine.

GRVI definition (common form):
  GRVI = (Green - Red) / (Green + Red)

Sentinel-2 SR Harmonized bands:
  Green = B3
  Red   = B4

This script:
  - Reads site polygons from data/sites.geojson
  - Reads monthly dates from data/measurements.csv (expects one row per site per month)
  - Computes monthly median GRVI for each site polygon over each month
  - Writes the result to the 'grvi' column in data/measurements.csv

Run (from project root):
  1) pip install -r requirements.txt
  2) earthengine authenticate
  3) python scripts/add_grvi_from_gee.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
import pandas as pd
import numpy as np

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SITES_GEOJSON = DATA_DIR / "sites.geojson"
MEASUREMENTS_CSV = DATA_DIR / "measurements.csv"

# ------------------ EE init ------------------
def _init_ee():
    try:
        import ee  # type: ignore
    except Exception as e:
        raise SystemExit(
            "Missing dependency 'earthengine-api'. Install with:\n"
            "  pip install earthengine-api\n"
            "or:\n"
            "  pip install -r requirements.txt\n"
        ) from e

    try:
        ee.Initialize()
    except Exception:
        raise SystemExit(
            "Earth Engine is not authenticated.\n"
            "Run:\n"
            "  earthengine authenticate\n"
            "then rerun this script."
        )
    return ee

# ------------------ GeoJSON -> ee.FeatureCollection ------------------
def _geojson_to_ee_fc(ee, geojson_path: Path):
    gj = json.loads(geojson_path.read_text(encoding="utf-8"))
    feats = []
    for f in gj["features"]:
        props = f.get("properties", {}) or {}
        site_id = props.get("site_id") or props.get("id") or props.get("name")
        if not site_id:
            raise ValueError("Each feature must have properties.site_id (or id/name).")
        feats.append(ee.Feature(ee.Geometry(f["geometry"]), {"site_id": site_id}))
    return ee.FeatureCollection(feats)

# ------------------ Month helpers ------------------
def _month_starts(dates: pd.Series) -> List[pd.Timestamp]:
    ds = pd.to_datetime(dates).dt.to_period("M").dropna().unique()
    return [p.to_timestamp() for p in sorted(ds)]

def _month_end(start: pd.Timestamp) -> pd.Timestamp:
    return start + pd.offsets.MonthBegin(1)

# ------------------ Sentinel-2 cloud masking ------------------
def _mask_s2_sr(img):
    # SCL-based mask (simple + robust)
    scl = img.select("SCL")
    mask = (
        scl.neq(3)   # cloud shadow
           .And(scl.neq(8))   # cloud medium prob
           .And(scl.neq(9))   # cloud high prob
           .And(scl.neq(10))  # thin cirrus
           .And(scl.neq(11))  # snow/ice
    )
    return img.updateMask(mask)

def _monthly_grvi_image(ee, start: str, end: str):
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterDate(start, end)
    s2 = s2.map(_mask_s2_sr)
    # monthly median reflectance
    med = s2.median()
    green = med.select("B3")
    red = med.select("B4")
    grvi = green.subtract(red).divide(green.add(red)).rename("grvi")
    return grvi

def _reduce_over_sites(ee, img, fc, scale_m: int = 20) -> Dict[str, float]:
    reduced = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale_m)
    feats = reduced.getInfo()["features"]
    out: Dict[str, float] = {}
    for f in feats:
        props = f.get("properties", {}) or {}
        sid = props.get("site_id")
        if sid:
            out[sid] = props.get("grvi")
    return out

def main():
    ee = _init_ee()
    df = pd.read_csv(MEASUREMENTS_CSV)
    if "grvi" not in df.columns:
        df["grvi"] = np.nan

    fc = _geojson_to_ee_fc(ee, SITES_GEOJSON)
    months = _month_starts(df["date"])

    for m0 in months:
        m1 = _month_end(m0)
        start = m0.strftime("%Y-%m-%d")
        end = m1.strftime("%Y-%m-%d")
        print(f"Computing GRVI for {start} .. {end}")

        img = _monthly_grvi_image(ee, start, end)
        vals = _reduce_over_sites(ee, img, fc)

        mask_month = (pd.to_datetime(df["date"]) == m0)
        for sid, v in vals.items():
            mask = mask_month & (df["site_id"] == sid)
            df.loc[mask, "grvi"] = v

    df.to_csv(MEASUREMENTS_CSV, index=False)
    print(f"Updated: {MEASUREMENTS_CSV}")

if __name__ == "__main__":
    main()
