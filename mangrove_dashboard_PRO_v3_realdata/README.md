# Guyana Coastal Mangrove Monitoring Dashboard (Streamlit)

This is a **stakeholder-ready** interactive GIS dashboard for 3 coastal study sites.

**What you get**
- Basemap toggle: OpenStreetMap / Light / Esri Satellite
- Interactive polygons (click to select a site)
- Time-series charts
- Monthly indices: **NDVI, MNDWI, GRVI**
- Monthly drivers: **salinity, SST, sea-level (as anomaly), air temperature, rainfall**
- Tide is included as a column, but is **not automatically fetched** (see Tide note below)

---

## 1) Unzip the bundle

Unzip this project to a folder, e.g.

`C:\Users\<you>\Documents\mangrove_dashboard_PRO_v3_realdata\`

Inside you should see:
- `app.py`
- `requirements.txt`
- `data/` (sites.geojson, measurements.csv)
- `scripts/`

---

## 2) Create a Python environment + install requirements

Open a terminal **in the project folder** and run:

### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3) Authenticate Google Earth Engine (one-time per machine)

This project fetches **real** monthly salinity/SST/sea-level/air-temp/rainfall via **Google Earth Engine**.

Run:
```bash
earthengine authenticate
```

Then:
```bash
python -c "import ee; ee.Initialize(); print('EE OK')"
```

---

## 4) Refresh the data (recommended)

### 4A) Add GRVI (computed from Sentinel-2 SR monthly median)
```bash
python scripts/add_grvi_from_gee.py
```

### 4B) Fetch monthly drivers (real data)
```bash
python scripts/fetch_ocean_met_gee_monthly.py
```

These scripts update:
- `data/measurements.csv`

> If a site polygon doesn’t overlap the relevant dataset (e.g., fully inland vs ocean),
> values may be blank (NaN). That’s expected.

---

## 5) Run the dashboard

```bash
streamlit run app.py
```

Streamlit will print a local URL (typically http://localhost:8501).

---

## Tide note (important)

**Tide ≠ sea level anomaly.**  
The script fetches **sea surface height anomaly** (via Copernicus sea surface height 'zos' turned into an anomaly relative to a baseline mean).

To add **tide** properly you will need either:
- a tide-gauge time series (station API), **or**
- a global tidal model (e.g., FES/TPXO) and local sampling.

When you’re ready, I can plug tide into the same `measurements.csv` workflow once you confirm your preferred tide source.

---

## Data structure

The dashboard reads:
- `data/sites.geojson`  (polygons with `site_id`)
- `data/measurements.csv` (one row per `site_id` per month)

You can replace/update `measurements.csv` at any time as long as you keep the column names.


## CCI (Coupling Coordination Index)

The dashboard computes **CCI** exactly as defined in *Hamer et al. (2024)*:

- **CCI = (NDVI + MNDWI) / 2**

GRVI is displayed separately and is not used in CCI unless you explicitly define a new variant.
