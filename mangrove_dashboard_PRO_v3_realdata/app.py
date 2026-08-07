import json
from pathlib import Path
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import folium
from streamlit_folium import st_folium

BASE_DIR = Path(__file__).parent
ASSETS = BASE_DIR / "assets"

# Branding assets (UG / FEES)
ug = ASSETS / "ug_logo.jpg"
fees = ASSETS / "fees_logo.png"




# --- Display helper: collapse duplicate leading acronyms in site IDs (e.g., MR_MR_..., BV_BV_...) ---
def clean_site_name_v2(s):
    s = str(s)
    import re as _re
    while True:
        m = _re.match(r"^([A-Za-z0-9]+)_\1_(.+)$", s)
        if not m:
            break
        s = f"{m.group(1)}_{m.group(2)}"
    return s


def clean_site_name(s):
    # Convert e.g. LBI_LBI_La_Bonne -> LBI_La_Bonne
    parts = s.split("_")
    if len(parts) > 2 and parts[0] == parts[1]:
        return "_".join([parts[0]] + parts[2:])
    return s

APP_TITLE = "Guyana Coastal Mangrove Monitoring Dashboard"
APP_SUBTITLE = "100m polygons - NDVI / MNDWI / CCI (per Hamer et al., 2024) - GRVI - Salinity / SST / Air Temperature / Tide"

DATA_DIR = Path(__file__).parent / "data"
SITES_FP = DATA_DIR / "sites.geojson"
MEAS_FP  = DATA_DIR / "measurements.csv"

# -------------------- Page config + styling --------------------
st.set_page_config(page_title=APP_TITLE, page_icon="🌿", layout="wide")

CUSTOM_CSS = """
<style>
.block-container {
    padding-top: 3rem;
    padding-bottom: 2.5rem;
}
[data-testid="stMetricValue"] {
    font-size: 1.4rem;
}
.small-muted {
    color: rgba(49, 51, 63, 0.65);
    font-size: 0.9rem;
}
hr {
    margin: 0.6rem 0 1.2rem 0;
}

/* Clear, readable tab labels */
.stTabs [data-baseweb="tab"] {
    color: #1f2937 !important;
    font-weight: 600 !important;
    font-size: 15px !important;
}
.stTabs [aria-selected="true"] {
    color: #b71c1c !important;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# Main dashboard heading
st.title(APP_TITLE)
st.caption(APP_SUBTITLE)
st.divider()

# -------------------- Utilities --------------------
@st.cache_data(show_spinner=False)
def load_sites_geojson(fp: Path) -> dict:
    return json.loads(fp.read_text(encoding="utf-8"))

@st.cache_data(show_spinner=False)
def load_measurements(fp: Path) -> pd.DataFrame:
    df = pd.read_csv(fp)

    required = {"site_id", "date", "ndvi", "mndwi"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"measurements.csv missing required columns: {sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()

    # Parse numerics if present
    numeric_cols = [
        "ndvi","mndwi","grvi",
        "salinity_psu","sst_c","ssh_m",
        "air_temp_c","rain_mm","tide_m"
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Compute CCI following Hamer et al. (2024): CCI = (NDVI + MNDWI) / 2
    # (Keep GRVI separate; it is displayed but not used in CCI unless you define a new variant.)
    df["cci"] = (df["ndvi"] + df["mndwi"]) / 2.0

    # Simple QA flags
    for c in ["ndvi","mndwi","grvi","cci"]:
        if c in df.columns:
            df[f"{c}_flag_out_of_range"] = (df[c] < -1) | (df[c] > 1)

    return df

def site_prefix(site_id: str) -> str:
    return str(site_id).split("_")[0]

def add_basemaps(m: folium.Map):
    # Basemaps (toggleable)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True).add_to(m)
    folium.TileLayer("CartoDB positron", name="Light (CartoDB)", control=True).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri",
        name="Esri Satellite",
        overlay=False,
        control=True,
    ).add_to(m)


def cci_bucket(v: float|None) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "No data"
    if v < 0.20:
        return "Low"
    if v <= 0.50:
        return "Moderate"
    return "High"

def cci_color(v: float|None) -> str:
    # Simple, stakeholder-friendly discrete colors
    b = cci_bucket(v)
    if b == "Low":
        return "#d73027"   # red
    if b == "Moderate":
        return "#fee08b"   # amber
    if b == "High":
        return "#1a9850"   # green
    return "#9e9e9e"      # gray


def make_map(sites_geojson: dict, selected_site_id: str|None, cci_by_site: dict|None=None):
    # Center on Guyana coast (fallback to feature bounds)
    m = folium.Map(location=[6.6, -58.2], zoom_start=9, control_scale=True, tiles=None)
    add_basemaps(m)

    # Polygons layer (drawn as folium.Polygon objects so streamlit-folium can serialize safely)
    for f in sites_geojson.get('features', []):
        props = f.get('properties', {})
        sid = str(props.get('site_id', ''))
        name = str(props.get('name', sid))
        geom = f.get('geometry', {})
        coords = geom.get('coordinates', [])
        if geom.get('type') == 'Polygon' and coords and coords[0]:
            ring = coords[0]  # outer ring
            latlon = [(pt[1], pt[0]) for pt in ring]
        elif geom.get('type') == 'MultiPolygon' and coords and coords[0] and coords[0][0]:
            ring = coords[0][0]
            latlon = [(pt[1], pt[0]) for pt in ring]
        else:
            continue

        is_sel = (selected_site_id == sid)
        cci_val = None
        if cci_by_site is not None:
            cci_val = cci_by_site.get(sid)

        stroke = '#d62728' if is_sel else '#1f77b4'
        fill = cci_color(cci_val) if cci_by_site is not None else (stroke if is_sel else '#1f77b4')
        fill_op = 0.55 if (cci_by_site is not None and is_sel) else (0.35 if cci_by_site is not None else (0.18 if is_sel else 0.10))

        tooltip_html = f"<b>{name}</b><br/>site_id: {sid}"
        if cci_by_site is not None:
            bucket = cci_bucket(cci_val)
            if cci_val is None:
                tooltip_html += f"<br/>CCI: n/a ({bucket})"
            else:
                tooltip_html += f"<br/>CCI: {float(cci_val):.3f} ({bucket})"

        folium.Polygon(
            locations=latlon,
            color=stroke,
            weight=2 if is_sel else 1,
            fill=True,
            fill_color=fill,
            fill_opacity=fill_op,
            tooltip=folium.Tooltip(tooltip_html, sticky=True),
        ).add_to(m)


    # Add centroids as markers for click selection
    for f in sites_geojson.get("features", []):
        props = f.get("properties", {})
        sid = str(props.get("site_id"))
        name = str(props.get("name", sid))
        geom = f.get("geometry", {})
        # centroid approximation from polygon coords
        coords = geom.get("coordinates", [])
        if geom.get("type") == "Polygon" and coords and coords[0]:
            xs = [c[0] for c in coords[0]]
            ys = [c[1] for c in coords[0]]
            lon = float(np.mean(xs)); lat = float(np.mean(ys))
        else:
            continue

        is_sel = (selected_site_id == sid)
        folium.CircleMarker(
            location=[lat, lon],
            radius=7 if is_sel else 5,
            color="#d62728" if is_sel else "#1f77b4",
            fill=True,
            fill_opacity=0.95,
            popup=folium.Popup(f"<b>{name}</b><br/>site_id: {sid}", max_width=280),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    return m

def line_chart(df: pd.DataFrame, y_cols: list[str], title: str, y_label: str|None=None):
    plot_df = df[["date"] + y_cols].melt("date", var_name="Variable", value_name="Value")
    plot_df = plot_df.dropna(subset=["Value"])
    if plot_df.empty:
        st.info("No data available for this chart yet.")
        return
    fig = px.line(plot_df, x="date", y="Value", color="Variable", markers=True, title=title)
    fig.update_layout(margin=dict(l=10,r=10,t=50,b=10), height=360)
    if y_label:
        fig.update_yaxes(title=y_label)
    st.plotly_chart(fig, use_container_width=True)


def dual_axis_chart(df: pd.DataFrame, left_cols: list[str], right_cols: list[str], title: str,
                    left_y_title: str = "Value", right_y_title: str = "Value"):
    # Keep only available columns and drop NaNs
    cols = ["date"] + [c for c in left_cols + right_cols if c in df.columns]
    if len(cols) <= 1:
        st.info("No data available for this chart yet.")
        return
    sdf = df[cols].copy()
    sdf = sdf.sort_values("date")
    if sdf.drop(columns=["date"]).dropna(how="all").empty:
        st.info("No data available for this chart yet.")
        return

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    color_map = {
        "salinity_psu": "#1f77b4",
        "sst_c": "#ff7f0e",
        "ssh_m": "#d62728",
        "air_temp_c": "#2ca02c",
        "rain_mm": "#9467bd",
        "tide_m": "#8c564b",
    }

    def add_series(col, secondary):
        if col not in sdf.columns:
            return
        series = sdf[["date", col]].dropna()
        if series.empty:
            return
        fig.add_trace(
            go.Scatter(
                x=series["date"],
                y=series[col],
                mode="lines+markers",
                name=col,
                line=dict(color=color_map.get(col)),
            ),
            secondary_y=secondary,
        )

    for c in left_cols:
        add_series(c, secondary=False)
    for c in right_cols:
        add_series(c, secondary=True)

    if len(fig.data) == 0:
        st.info("No data available for this chart yet.")
        return

    fig.update_layout(
        title=title,
        margin=dict(l=10, r=10, t=50, b=10),
        height=360,
        legend_title_text="Variable",
    )
    fig.update_yaxes(title_text=left_y_title, secondary_y=False)
    fig.update_yaxes(title_text=right_y_title, secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)


# -------------------- Load data --------------------
sites_geojson = load_sites_geojson(SITES_FP)
meas = load_measurements(MEAS_FP)

# Build mapping between polygon ids (full) and measurement ids (short prefix)
# sites.geojson includes properties: site_id (full) and prefix (short)
full_to_short = {}
short_to_full = {}
for feat in sites_geojson.get("features", []):
    props = feat.get("properties", {}) or {}
    full = str(props.get("site_id", "")).strip()
    short = str(props.get("prefix", "")).strip()
    if full and short:
        full_to_short[full] = short
        # keep first if duplicates
        short_to_full.setdefault(short, full)

def resolve_measurement_site_id(selected_full_id: str) -> str:
    """Map UI polygon site_id (full) to measurements site_id (short).
    Falls back to selected_full_id if already present in measurements."""
    if selected_full_id in set(meas["site_id"].astype(str)):
        return selected_full_id
    return full_to_short.get(selected_full_id, selected_full_id)

# Sidebar filters

# -------------------- Selection state (no global sidebar) --------------------
# Keep controls off the About and Live Satellite App tabs, but always define
# selected site and site_df so the app never crashes.
if 'selected_full_site' not in st.session_state:
    # Default to first site in sites.geojson; fallback to first measurements site_id
    _default_full = None
    for _feat in sites_geojson.get('features', []):
        _props = _feat.get('properties', {}) or {}
        _sid = str(_props.get('site_id', '')).strip()
        if _sid:
            _default_full = _sid
            break
    if _default_full is None and 'site_id' in meas.columns and len(meas):
        _default_full = str(meas['site_id'].astype(str).iloc[0])
    st.session_state['selected_full_site'] = _default_full or ''

selected_full_site = st.session_state.get('selected_full_site', '')

selected_site = selected_full_site  # alias for legacy code
selected_meas_site = resolve_measurement_site_id(selected_full_site) if selected_full_site else ''
site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()

# Default date range for filtering (based on selected site)
if 'date_range' not in st.session_state:
    if 'date' in site_df.columns and len(site_df):
        st.session_state['date_range'] = (site_df['date'].min().date(), site_df['date'].max().date())
    else:
        st.session_state['date_range'] = None
# -----------------------------------------------------------------------------

def get_filtered_site_df() -> pd.DataFrame:
    """Return selected site_df filtered by session date_range (if set)."""
    sdf = site_df.copy()
    dr = st.session_state.get('date_range', None)
    if dr and 'date' in sdf.columns and len(sdf):
        try:
            start_d, end_d = dr
            sdf = sdf[(sdf['date'].dt.date >= start_d) & (sdf['date'].dt.date <= end_d)].copy()
        except Exception:
            pass
    return sdf


tab_welcome, tab_overview, tab_map, tab_indices, tab_oceanmet, tab_data, tab_gee, tab_about = st.tabs(
    ["Welcome", "Overview", "Interactive Map", "Vegetation Indices", "Ocean / Met", "Data & QA", "Live Satellite App", "About"]
)


with tab_welcome:
    st.markdown("""
    # Welcome to the Guyana Coastal Mangrove Monitoring Dashboard
    ### Safeguarding Guyana's coast through science, data, and decision support

    Guyana's mangrove ecosystems form one of the most important natural defense systems along the Atlantic coast of South America.
    These coastal forests protect low-lying communities from storm surge and erosion, support fisheries and livelihoods, store large quantities of carbon,
    and stabilize the shoreline against rising sea levels. In a country where much of the population, infrastructure, and agriculture lie below or near sea level,
    healthy mangroves are essential to national resilience.

    However, Guyana's mangroves are under increasing pressure from climate change, sea-level rise, changing river flows, coastal development, and land-use conversion.
    These pressures can lead to **coastal squeeze**, where mangrove forests are trapped between rising seas on the seaward side and hard infrastructure
    (sea defenses, roads, farms, settlements) on the landward side. When mangroves cannot migrate inland, long-term loss of protective coastal vegetation can occur.
    """)

    st.markdown("""
    ## What this dashboard does
    This dashboard provides a science-based system for monitoring, diagnosing, and tracking the health of Guyana's mangrove systems over time using satellite data
    and field-linked indicators.
    """)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        **Core ecosystem indicators**
        - Vegetation condition (NDVI, GRVI)
        - Surface water and inundation (MNDWI)
        - Eco-hydrological coupling (CCI)
        """)
    with c2:
        st.markdown("""
        **Drivers and supporting data**
        - Salinity
        - Sea-surface temperature (SST)
        - Air temperature
        - Tide
        """)

    st.markdown("""
    ## How this helps manage coastal squeeze
    Coastal squeeze is not only a visual problem - it is measurable.

    By combining vegetation greenness (NDVI/GRVI) with surface water dynamics (MNDWI) into the **Coastal Coupling Index (CCI)**, the dashboard helps quantify whether
    mangroves are functioning as healthy, water-adapted coastal ecosystems, or becoming hydrologically disconnected and ecologically stressed.

    Early warning patterns can include:
    - Declining vegetation indices
    - Increasing or abnormal water presence
    - Weakening or unstable vegetation-water coupling (lower or highly variable CCI)
    """)

    st.markdown("""
    ## Who this platform is for
    - **University of Guyana** - teaching, research, and long-term monitoring
    - **Government agencies** - coastal zone management, planning, and reporting
    - **Development partners (e.g., GGGI)** - evidence-based Nature-based Solutions (NbS) planning and evaluation
    """)

    st.info("Use the tabs above to explore maps, time-series trends, and drivers. For full-resolution satellite rendering, use the Live Satellite App tab.")


with tab_overview:

    with st.expander("Controls", expanded=False):
        _full_sites = sorted(list(full_to_short.keys())) if len(full_to_short) else sorted(site_df['site_id'].astype(str).unique().tolist()) if 'site_id' in site_df.columns else []
        if len(_full_sites) == 0 and 'site_id' in meas.columns:
            _full_sites = sorted(meas['site_id'].astype(str).unique().tolist())
        if len(_full_sites) > 0:
            _sel = st.selectbox("Study site", _full_sites, index=max(0, _full_sites.index(str(selected_full_site)) if str(selected_full_site) in _full_sites else 0), key='ov_site')
            if str(_sel) != str(st.session_state.get('selected_full_site','')):
                st.session_state['selected_full_site'] = str(_sel)
                selected_full_site = str(_sel)
                selected_meas_site = resolve_measurement_site_id(selected_full_site)
                site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()
        if 'date' in site_df.columns and len(site_df):
            _min_d = site_df['date'].min().date()
            _max_d = site_df['date'].max().date()
            _dr = st.date_input("Date range", value=(st.session_state.get('date_range') or (_min_d,_max_d)), key="ov_date_range")
            st.session_state['date_range'] = _dr

    site_df = get_filtered_site_df()


    c1, c2, c3, c4, c5 = st.columns(5)
    latest = site_df.sort_values("date").tail(1)
    def metric(col, label, fmt="{:.3f}"):
        if col in site_df.columns and not latest.empty and pd.notna(latest.iloc[0][col]):
            return fmt.format(float(latest.iloc[0][col]))
        return "-"

    c1.metric("Latest NDVI", metric("ndvi","NDVI"), help="Normalized Difference Vegetation Index")
    c2.metric("Latest MNDWI", metric("mndwi","MNDWI"), help="Modified Normalized Difference Water Index")
    c3.metric("Latest CCI", metric("cci","CCI"), help="Coupling Coordination Index: (NDVI + MNDWI) / 2 (Hamer et al., 2024)")
    c4.metric("Latest GRVI", metric("grvi","GRVI"), help="Green-Red Vegetation Index: (Green-Red)/(Green+Red); displayed separately")
    c5.metric("Latest Salinity (psu)", metric("salinity_psu","Salinity", fmt="{:.2f}"), help="Satellite/Model-derived; validate with in-situ if available")

    st.markdown("**What this dashboard does**")
    st.write(
        "Use the map to inspect the three coastal study sites, then explore time-series of vegetation indices "
        "(NDVI, MNDWI, GRVI) alongside ocean-meteorology drivers (salinity, SST, air temperature, tide). "
        "Data are stored in `data/measurements.csv` and can be refreshed via the scripts in `scripts/`."
    )

with tab_map:

    with st.expander("Controls", expanded=False):
        _full_sites = sorted(list(full_to_short.keys())) if len(full_to_short) else sorted(site_df['site_id'].astype(str).unique().tolist()) if 'site_id' in site_df.columns else []
        if len(_full_sites) == 0 and 'site_id' in meas.columns:
            _full_sites = sorted(meas['site_id'].astype(str).unique().tolist())
        if len(_full_sites) > 0:
            _sel = st.selectbox("Study site", _full_sites, index=max(0, _full_sites.index(str(selected_full_site)) if str(selected_full_site) in _full_sites else 0), key='map_site')
            if str(_sel) != str(st.session_state.get('selected_full_site','')):
                st.session_state['selected_full_site'] = str(_sel)
                selected_full_site = str(_sel)
                selected_meas_site = resolve_measurement_site_id(selected_full_site)
                site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()
        if 'date' in site_df.columns and len(site_df):
            _min_d = site_df['date'].min().date()
            _max_d = site_df['date'].max().date()
            _dr = st.date_input("Date range", value=(st.session_state.get('date_range') or (_min_d,_max_d)), key="map_date_range")
            st.session_state['date_range'] = _dr

    site_df = get_filtered_site_df()


    st.subheader("Study Areas (100m polygons)")

    # Choose which month to paint on the map using CCI
    available_months = sorted(meas["date"].dt.to_period("M").unique())
    if available_months:
        default_month = available_months[-1]
        sel_month = st.selectbox(
            "Map month (for CCI layer)",
            available_months,
            index=len(available_months)-1,
            format_func=lambda p: str(p),
        )
        month_start = sel_month.to_timestamp()
        # Filter to the selected month and pick the (single) monthly record per site
        month_df = meas[meas["date"].dt.to_period("M") == sel_month][["site_id","cci"]].dropna(subset=["site_id"])
        cci_by_site = {}
        for _, r in month_df.iterrows():
            short_id = str(r["site_id"])
            full_id = short_to_full.get(short_id, short_id)
            cci_by_site[full_id] = (None if pd.isna(r["cci"]) else float(r["cci"]))
    else:
        st.info("No dates found in measurements.csv yet.")
        cci_by_site = None

    m = make_map(sites_geojson, selected_site, cci_by_site=cci_by_site)
    st_folium(m, height=560, use_container_width=True)

    st.markdown(
        """<div class='small-muted'><b>CCI map legend:</b> Low (&lt; 0.20) - Moderate (0.20-0.50) - High (&gt; 0.50). 
        CCI is computed as (NDVI + MNDWI)/2 following Hamer et al. (2024).</div>""",
        unsafe_allow_html=True,
    )

with tab_indices:

    with st.expander("Controls", expanded=False):
        _full_sites = sorted(list(full_to_short.keys())) if len(full_to_short) else sorted(site_df['site_id'].astype(str).unique().tolist()) if 'site_id' in site_df.columns else []
        if len(_full_sites) == 0 and 'site_id' in meas.columns:
            _full_sites = sorted(meas['site_id'].astype(str).unique().tolist())
        if len(_full_sites) > 0:
            _sel = st.selectbox("Study site", _full_sites, index=max(0, _full_sites.index(str(selected_full_site)) if str(selected_full_site) in _full_sites else 0), key='idx_site')
            if str(_sel) != str(st.session_state.get('selected_full_site','')):
                st.session_state['selected_full_site'] = str(_sel)
                selected_full_site = str(_sel)
                selected_meas_site = resolve_measurement_site_id(selected_full_site)
                site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()
        if 'date' in site_df.columns and len(site_df):
            _min_d = site_df['date'].min().date()
            _max_d = site_df['date'].max().date()
            _dr = st.date_input("Date range", value=(st.session_state.get('date_range') or (_min_d,_max_d)), key="idx_date_range")
            st.session_state['date_range'] = _dr

    site_df = get_filtered_site_df()


    st.subheader(f"Vegetation Indices - {selected_site}")

    left, right = st.columns(2)
    with left:
        # CCI per your paper: (NDVI + MNDWI)/2
        core = [c for c in ["ndvi","mndwi","cci"] if c in site_df.columns]
        line_chart(site_df, core, "NDVI / MNDWI / CCI (Hamer et al., 2024)", y_label="Index value (unitless)")

        st.markdown(
            "<div class='small-muted'><b>CCI legend (stakeholder view):</b> "
            "Low (&lt; 0.20) - Moderate (0.20-0.50) - High (&gt; 0.50)</div>",
            unsafe_allow_html=True,
        )

    with right:
        # GRVI is displayed separately (not used in CCI unless you define a new variant)
        if "grvi" in site_df.columns:
            line_chart(site_df, ["grvi"], "GRVI (displayed separately)", y_label="Index value (unitless)")
            st.caption("GRVI requires Green and Red reflectance; populate via the GEE export workflow.")
        else:
            st.info("GRVI column not found yet. Run the GRVI export + merge scripts to populate it.")

with tab_oceanmet:

    with st.expander("Controls", expanded=False):
        _full_sites = sorted(list(full_to_short.keys())) if len(full_to_short) else sorted(site_df['site_id'].astype(str).unique().tolist()) if 'site_id' in site_df.columns else []
        if len(_full_sites) == 0 and 'site_id' in meas.columns:
            _full_sites = sorted(meas['site_id'].astype(str).unique().tolist())
        if len(_full_sites) > 0:
            _sel = st.selectbox("Study site", _full_sites, index=max(0, _full_sites.index(str(selected_full_site)) if str(selected_full_site) in _full_sites else 0), key='oc_site')
            if str(_sel) != str(st.session_state.get('selected_full_site','')):
                st.session_state['selected_full_site'] = str(_sel)
                selected_full_site = str(_sel)
                selected_meas_site = resolve_measurement_site_id(selected_full_site)
                site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()
        if 'date' in site_df.columns and len(site_df):
            _min_d = site_df['date'].min().date()
            _max_d = site_df['date'].max().date()
            _dr = st.date_input("Date range", value=(st.session_state.get('date_range') or (_min_d,_max_d)), key="oc_date_range")
            st.session_state['date_range'] = _dr

    site_df = get_filtered_site_df()


    st.subheader(f"Ocean / Meteorology Drivers - {selected_site}")

    left, right = st.columns(2)
    with left:
        dual_axis_chart(site_df, left_cols=["salinity_psu","sst_c"], right_cols=["ssh_m"], title="Salinity / SST (left axis) - Sea Surface Height (right axis)", left_y_title="Salinity (psu) / SST (°C)", right_y_title="SSH (m)")
    with right:
        dual_axis_chart(site_df, left_cols=["rain_mm"], right_cols=["air_temp_c","tide_m"], title="Rainfall (left axis) - Air temperature / Tide (right axis)", left_y_title="Rainfall (mm, monthly total)", right_y_title="Air temperature (°C) / Tide (m)")

    st.markdown("**Notes**")
    st.write(
        """- Tide is not included in the current pipeline. It can be added using a global tidal model (e.g., FES/TPXO) or a nearby tide gauge API.
- Salinity/SST/SLA/air temperature/rain scripts in `scripts/` can be scheduled (e.g., monthly refresh) to keep driver variables up to date."""
    )

with tab_data:

    with st.expander("Controls", expanded=False):
        _full_sites = sorted(list(full_to_short.keys())) if len(full_to_short) else sorted(site_df['site_id'].astype(str).unique().tolist()) if 'site_id' in site_df.columns else []
        if len(_full_sites) == 0 and 'site_id' in meas.columns:
            _full_sites = sorted(meas['site_id'].astype(str).unique().tolist())
        if len(_full_sites) > 0:
            _sel = st.selectbox("Study site", _full_sites, index=max(0, _full_sites.index(str(selected_full_site)) if str(selected_full_site) in _full_sites else 0), key='dq_site')
            if str(_sel) != str(st.session_state.get('selected_full_site','')):
                st.session_state['selected_full_site'] = str(_sel)
                selected_full_site = str(_sel)
                selected_meas_site = resolve_measurement_site_id(selected_full_site)
                site_df = meas[meas['site_id'].astype(str) == str(selected_meas_site)].copy() if selected_meas_site else meas.copy()
        if 'date' in site_df.columns and len(site_df):
            _min_d = site_df['date'].min().date()
            _max_d = site_df['date'].max().date()
            _dr = st.date_input("Date range", value=(st.session_state.get('date_range') or (_min_d,_max_d)), key="dq_date_range")
            st.session_state['date_range'] = _dr

    site_df = get_filtered_site_df()


    st.subheader("Data preview")
    st.dataframe(site_df, use_container_width=True, height=360)

    st.subheader("QA flags")
    flags = [c for c in site_df.columns if c.endswith("_flag_out_of_range")]
    if flags:
        st.write(site_df[["date"] + flags].tail(12))
    else:
        st.info("No QA flags present.")


    # -------------------- ABOUT TAB --------------------

with tab_gee:
    st.subheader("Live Satellite App")
    st.markdown("Open the Google Earth Engine app in a new tab using the button or QR code below.")

    if hasattr(st, "link_button"):
        st.link_button("Open Live Satellite App (New Tab)", "https://esanhamer.users.earthengine.app/view/vegetation-monitoring-indices")
    else:
        st.markdown("[Open Live Satellite App (New Tab)](https://esanhamer.users.earthengine.app/view/vegetation-monitoring-indices)")

    st.divider()
    left, right = st.columns([2, 1])

    with left:
        st.markdown("### Quick instructions")
        st.markdown(
            "1. Open the Live Satellite App in a new tab.\n"
            "2. Draw a polygon/rectangle over your site (AOI).\n"
            "3. Choose Year / Month / Index, then Render.\n"
            "4. Use the chart for monthly reporting and site comparison."
        )
        st.markdown("### Direct link")
        st.code("https://esanhamer.users.earthengine.app/view/vegetation-monitoring-indices", language="text")

    with right:
        st.markdown("### Scan to open")
        st.image(str(Path(__file__).parent / "assets" / "gee_qr.png"), width=250)

with tab_about:
    st.subheader("About this project")
    st.markdown(
        """This interactive GIS dashboard supports coastal mangrove monitoring across selected study areas on Guyana's coast.
It integrates remote-sensing vegetation indices with oceanographic and meteorological drivers to support assessment, reporting, and decision-making.

**CCI represents a remote-sensing based eco-hydrological coupling index, reflecting the joint spatial expression of vegetation greenness and surface water presence.**

**Core indicators**
- Vegetation indices: NDVI, MNDWI, GRVI
- Coupling index: CCI (computed as **CCI = (NDVI + MNDWI) / 2**)
- Drivers: monthly accumulated rainfall, near-surface air temperature, sea-surface temperature, sea-surface salinity, and sea surface height
"""
    )

    st.divider()
    st.subheader("Research team")
    c1, c2, c3, c4 = st.columns(4)
    base = Path(__file__).parent / "assets"
    def _person(col, img, name, dept):
        with col:
            img_path = base / img
            if img_path.exists():
                st.image(str(img_path), width=180)
            st.markdown(f"**{name}**  \n{dept}")

    _person(c1, "esan.jpg", "Mr. Esan Ayeni Hamer", "Department of Geography")
    _person(c2, "collis.jpg", "Mr. Collis Allen", "Department of Environmental Studies")
    _person(c3, "ronn.jpg", "Mr. Ronn Sullivan", "Department of Environmental Studies")
    _person(c4, "seion.jpg", "Mr. Seion Britton", "Department of Environmental Studies")

    st.divider()
    st.subheader("Data sources (monthly)")
    st.markdown(
        """- **Landsat 8/9 TOA** - NDVI, MNDWI, GRVI  
- **CHIRPS** - Monthly accumulated rainfall (mm)  
- **ERA5-Land** - Monthly mean near-surface air temperature (°C)  
- **NOAA OISST** - Monthly mean sea-surface temperature (°C)  
- **Copernicus Marine Service** - Monthly mean sea-surface salinity (psu) and sea surface height (m)  
"""
    )



    # --- About footer (Institutional affiliation) ---
    st.markdown(
        """<style>
        .about-footer{
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid rgba(0,0,0,0.08);
        }
        .about-footer .muted{color: rgba(0,0,0,0.6); font-size: 0.9rem;}
        </style>""",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="about-footer">', unsafe_allow_html=True)
    st.subheader("Institutional affiliation")
    c1, c2 = st.columns([1, 1])
    with c1:
        if fees.exists():
            st.image(str(fees), width=72)
        st.caption("Faculty of Earth and Environmental Sciences (FEES)")
    with c2:
        if ug.exists():
            st.image(str(ug), width=72)
        st.caption("University of Guyana")
    st.markdown(
        '<div class="muted">© Copyright 2026 University of Guyana, Turkeyen, Greater Georgetown, Guyana, South America.</div></div>',
        unsafe_allow_html=True,
    )
    # --- End footer ---

