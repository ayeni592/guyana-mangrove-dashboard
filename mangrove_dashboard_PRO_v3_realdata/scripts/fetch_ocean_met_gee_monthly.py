"""
Fetch REAL monthly environmental drivers for each site polygon using Google Earth Engine (GEE).

This script updates:
  data/measurements.csv

It fills/updates the columns:
  salinity_psu, sst_c, sla_m, airtemp_c, rain_mm

And leaves:
  tide_m  (blank/NaN by default; see notes below)

Datasets used (public Earth Engine catalog):
  • Salinity (psu, daily): COPERNICUS/MARINE/GLOBAL_SEA_SURFACE/NRT_DAILY band 'sos'
  • Sea surface height (m, daily): COPERNICUS/MARINE/GLOBAL_ANALYSISFORECAST_PHY_DAILY band 'zos'
      - We convert this to an anomaly (sla_m) by subtracting a baseline mean per site.
  • SST (°C, daily): NOAA/CDR/OISST/V2_1 band 'sst' (scaled by 0.01)
  • Air temperature (K, monthly): ECMWF/ERA5_LAND/MONTHLY_AGGR band 'temperature_2m' (converted to °C)
  • Rainfall (mm/day, daily): UCSB-CHG/CHIRPS/DAILY band 'precipitation' (summed to monthly total)

Run (from project root):
  1) pip install -r requirements.txt
  2) earthengine authenticate
  3) python scripts/fetch_ocean_met_gee_monthly.py

Important notes / caveats:
  • Ocean products require the polygon to overlap ocean pixels. If your 100m square is fully inland,
    salinity/sea level/SST can return NaN (not an error).
  • CHIRPS is land-only; if a polygon is offshore, rainfall can return NaN.
  • Tide is not the same as sea level anomaly. Adding tide properly requires either:
      (A) a tide-gauge API/station time series, OR
      (B) a global tidal model (e.g., FES/TPXO) and local sampling.
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


def _init_ee():
    try:
        import ee  # type: ignore
    except Exception as e:
        raise SystemExit(
            "Missing dependency 'earthengine-api'. Install with:\n"
            "  pip install earthengine-api\n"
            "or simply:\n"
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


def _geojson_to_ee_fc(ee, geojson_path: Path):
    gj = json.loads(geojson_path.read_text(encoding="utf-8"))
    feats = []
    for f in gj["features"]:
        props = f.get("properties", {}) or {}
        site_id = props.get("site_id") or props.get("id") or props.get("name")
        if not site_id:
            raise ValueError("Each feature must have properties.site_id (or id/name).")
        geom = f["geometry"]
        feats.append(ee.Feature(ee.Geometry(geom), {"site_id": site_id}))
    return ee.FeatureCollection(feats)


def _month_starts(dates: pd.Series) -> List[pd.Timestamp]:
    ds = pd.to_datetime(dates).dt.to_period("M").dropna().unique()
    return [p.to_timestamp() for p in sorted(ds)]


def _month_end(start: pd.Timestamp) -> pd.Timestamp:
    return start + pd.offsets.MonthBegin(1)


def _get_monthly_driver_image(ee, start: str, end: str):
    """Return a multi-band image with monthly values for the month [start, end)."""
    start_date = ee.Date(start)
    end_date = ee.Date(end)

    # Salinity (Copernicus)
    sal = (
        ee.ImageCollection("COPERNICUS/MARINE/GLOBAL_SEA_SURFACE/NRT_DAILY")
        .filterDate(start_date, end_date)
        .select("sos")
        .mean()
        .rename("salinity_psu")
    )

    # Sea surface height (Copernicus)
    ssh = (
        ee.ImageCollection("COPERNICUS/MARINE/GLOBAL_ANALYSISFORECAST_PHY_DAILY")
        .filterDate(start_date, end_date)
        .select("zos")
        .mean()
        .rename("ssh_m")
    )

    # SST (NOAA OISST; scaled)
    sst = (
        ee.ImageCollection("NOAA/CDR/OISST/V2_1")
        .filterDate(start_date, end_date)
        .select("sst")
        .mean()
        .multiply(0.01)
        .rename("sst_c")
    )

    # Air temp (ERA5-Land monthly; Kelvin -> C)
    air = (
        ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
        .filterDate(start_date, end_date)
        .select("temperature_2m")
        .mean()
        .subtract(273.15)
        .rename("airtemp_c")
    )

    # Rainfall (CHIRPS daily; sum to monthly total)
    rain = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(start_date, end_date)
        .select("precipitation")
        .sum()
        .rename("rain_mm")
    )

    return ee.Image.cat([sal, ssh, sst, air, rain])


def _reduce_over_sites(ee, img, fc, scale_m: int = 10000) -> Dict[str, Dict[str, float]]:
    reduced = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale_m)
    features = reduced.getInfo()["features"]
    out: Dict[str, Dict[str, float]] = {}
    for f in features:
        props = f.get("properties", {}) or {}
        sid = props.get("site_id")
        if not sid:
            continue
        out[sid] = {
            "salinity_psu": props.get("salinity_psu"),
            "ssh_m": props.get("ssh_m"),
            "sst_c": props.get("sst_c"),
            "airtemp_c": props.get("airtemp_c"),
            "rain_mm": props.get("rain_mm"),
        }
    return out


def main():
    ee = _init_ee()
    if not MEASUREMENTS_CSV.exists():
        raise SystemExit(f"Missing {MEASUREMENTS_CSV}. Run from the project root.")

    df = pd.read_csv(MEASUREMENTS_CSV)
    for col in ["salinity_psu", "sst_c", "sla_m", "airtemp_c", "rain_mm", "tide_m"]:
        if col not in df.columns:
            df[col] = np.nan

    months = _month_starts(df["date"])
    if not months:
        raise SystemExit("No dates found in measurements.csv.")

    fc = _geojson_to_ee_fc(ee, SITES_GEOJSON)

    # Baseline for "SLA": mean SSH over a reference window per site
    # Choose a stable window that exists in Copernicus dataset (starts 2022-06-01).
    baseline_start = "2022-06-01"
    baseline_end = "2024-06-01"
    baseline_img = _get_monthly_driver_image(ee, baseline_start, baseline_end).select("ssh_m")
    baseline_vals = _reduce_over_sites(ee, baseline_img, fc)
    baseline_ssh = {sid: rec.get("ssh_m") for sid, rec in baseline_vals.items()}

    scale_m = 10000

    for m0 in months:
        m1 = _month_end(m0)
        start = m0.strftime("%Y-%m-%d")
        end = m1.strftime("%Y-%m-%d")
        print(f"Fetching drivers for {start} .. {end} (monthly)")

        img = _get_monthly_driver_image(ee, start, end)
        vals = _reduce_over_sites(ee, img, fc, scale_m=scale_m)

        mask_month = (pd.to_datetime(df["date"]) == m0)
        for site_id, rec in vals.items():
            mask = mask_month & (df["site_id"] == site_id)

            df.loc[mask, "salinity_psu"] = rec.get("salinity_psu")
            df.loc[mask, "sst_c"] = rec.get("sst_c")
            df.loc[mask, "airtemp_c"] = rec.get("airtemp_c")
            df.loc[mask, "rain_mm"] = rec.get("rain_mm")

            # SLA = SSH anomaly relative to baseline mean at the site
            b = baseline_ssh.get(site_id)
            ssh = rec.get("ssh_m")
            df.loc[mask, "sla_m"] = (ssh - b) if (ssh is not None and b is not None) else np.nan

    df.to_csv(MEASUREMENTS_CSV, index=False)
    print(f"Updated: {MEASUREMENTS_CSV}")
    print("tide_m remains blank (NaN). Add tides via tide gauge or tidal model when available.")


if __name__ == "__main__":
    main()
