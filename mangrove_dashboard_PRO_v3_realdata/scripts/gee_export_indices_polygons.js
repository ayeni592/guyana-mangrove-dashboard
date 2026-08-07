/**** Export monthly NDVI + MNDWI stats per 100m polygon (Sentinel-2 SR Harmonized)
 * Output: CSV with columns: site_id, date, ndvi, mndwi, n_pixels
 *
 * HOW TO USE:
 * 1) Upload your 100m polygons as an ee.FeatureCollection asset (must include 'site_id' property).
 * 2) Set ASSET_ID below.
 * 3) Set START/END.
 * 4) Run and click "Run" -> Tasks -> Export.table.toDrive
 ****/

var ASSET_ID = "users/YOUR_USERNAME/your_100m_polygons";  // <-- CHANGE THIS
var ID_FIELD = "site_id";                                 // <-- CHANGE if needed
var START = "2025-07-01";
var END   = "2025-12-31";

// Sentinel-2 SR Harmonized
var S2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED");

// Cloud mask using Scene Classification Layer (SCL)
function maskS2sr(img) {
  var scl = img.select("SCL");
  var mask = scl.neq(3)   // cloud shadow
    .and(scl.neq(8))      // cloud medium prob
    .and(scl.neq(9))      // cloud high prob
    .and(scl.neq(10))     // thin cirrus
    .and(scl.neq(11));    // snow/ice
  return img.updateMask(mask);
}

// Compute indices
function addIndices(img) {
  var ndvi  = img.normalizedDifference(["B8","B4"]).rename("ndvi");
  var mndwi = img.normalizedDifference(["B3","B11"]).rename("mndwi");
  // Green-Red Vegetation Index (GRVI): (G - R) / (G + R)
  var grvi  = img.normalizedDifference(["B3","B4"]).rename("grvi");
  return img.addBands([ndvi, mndwi, grvi]);
}

// Load polygons
var polys = ee.FeatureCollection(ASSET_ID);

// Month list
var startDate = ee.Date(START);
var endDate = ee.Date(END);
var nMonths = endDate.difference(startDate, "month").round();
var months = ee.List.sequence(0, nMonths.subtract(1));

// Build monthly composites and reduce to polygons
var perMonth = months.map(function(m) {
  m = ee.Number(m);
  var d0 = startDate.advance(m, "month");
  var d1 = d0.advance(1, "month");

  var ic = S2.filterDate(d0, d1)
    .filterBounds(polys)
    .map(maskS2sr)
    .map(addIndices);

  // Use median composite to reduce residual cloud contamination
  var comp = ic.median();

  // Reduce to polygons (mean)
  var reduced = comp.select(["ndvi","mndwi"]).reduceRegions({
    collection: polys,
    reducer: ee.Reducer.mean().combine({
      reducer2: ee.Reducer.count(),
      sharedInputs: true
    }),
    scale: 10
  });

  // Add month label + standard column names
  reduced = reduced.map(function(f) {
    return ee.Feature(null, {
      site_id: f.get(ID_FIELD),
      date: d0.format("YYYY-MM-01"),
      ndvi: f.get("ndvi_mean"),
      mndwi: f.get("mndwi_mean"),
      n_pixels: f.get("ndvi_count")
    });
  });

  return reduced;
});

var out = ee.FeatureCollection(perMonth).flatten();

// Export
Export.table.toDrive({
  collection: out,
  description: "NDVI_MNDWI_monthly_by_polygon",
  fileFormat: "CSV"
});
