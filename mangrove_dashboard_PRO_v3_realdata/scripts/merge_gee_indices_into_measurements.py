#!/usr/bin/env python
"""Merge GEE-exported indices (NDVI/MNDWI/GRVI) into data/measurements.csv.

Usage (from project root):
  python scripts/merge_gee_indices_into_measurements.py --indices_csv path/to/gee_export.csv

The script:
- parses dates
- harmonizes site_id using prefix matching against data/SITE_IDS.txt
- merges columns: ndvi, mndwi, grvi
"""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

def load_site_prefix_map(site_ids_fp: Path) -> dict[str,str]:
    prefixes={}
    for line in site_ids_fp.read_text(encoding='utf-8').splitlines():
        s=line.strip()
        if not s:
            continue
        prefixes[s.split('_')[0]] = s
    return prefixes

def harmonize_site_id(series: pd.Series, prefix_map: dict[str,str]) -> pd.Series:
    s = series.astype(str)
    prefix = s.str.split('_').str[0]
    return prefix.map(prefix_map).fillna(s)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--indices_csv', required=True, help='CSV exported from GEE with columns site_id,date,ndvi,mndwi,grvi')
    ap.add_argument('--measurements', default='data/measurements.csv')
    ap.add_argument('--out', default='data/measurements.csv')
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    meas_fp = root / args.measurements
    out_fp  = root / args.out
    idx_fp  = Path(args.indices_csv)

    meas = pd.read_csv(meas_fp)
    idx  = pd.read_csv(idx_fp)

    for df in (meas, idx):
        if 'date' not in df.columns:
            raise ValueError('Both measurements and indices must have a date column.')
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
    meas = meas.dropna(subset=['date'])
    idx  = idx.dropna(subset=['date'])

    # Required in idx
    required = {'site_id','ndvi','mndwi','grvi'}
    missing = required - set(idx.columns)
    if missing:
        raise ValueError(f'Indices CSV is missing required columns: {sorted(missing)}')

    prefix_map = load_site_prefix_map(root / 'data/SITE_IDS.txt')
    meas['site_id'] = harmonize_site_id(meas['site_id'], prefix_map)
    idx['site_id']  = harmonize_site_id(idx['site_id'], prefix_map)

    idx = idx[['site_id','date','ndvi','mndwi','grvi']].copy()

    merged = meas.drop(columns=[c for c in ['ndvi','mndwi','grvi'] if c in meas.columns]).merge(
        idx, on=['site_id','date'], how='left'
    )

    out_fp.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_fp, index=False)
    print(f'Wrote {out_fp} with {len(merged):,} rows.')

if __name__ == '__main__':
    main()
