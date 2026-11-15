# Data catalog

All files in `data/raw/` come from NASA (or U.S. Government) services and were downloaded on 2026-11-14 with the
scripts in `scripts/`. **`data/raw/MANIFEST.json`** lists every file with its exact source URL, size, SHA-256 and
download time — use it to fill the "NASA Data" field of the submission.

**In Git:** `gale/` (terrain, imagery, layers, traverse). **Not in Git (heavy):** `photos/`, `rad/`, `rems/` —
generate them with `python data/generacion/generar_todo.py` (see [`generacion/README.md`](generacion/README.md)).
The manifest also lists those files, so it documents their source even before they are generated.
Regenerate everything from scratch: `python scripts/download_all.py` (from the repo root).
Coordinates: decimal degrees, longitude East, on a spherical Mars (r = 3389.5 km, 1° ≈ 59.16 km).
Rasters: row 0 is the **north** edge; each raster has a `.json` sidecar with its exact bbox and pixel size.

## Areas (`configs/sites/gale.yaml`)

| Box | Lon | Lat | Pixel | What it is |
|---|---|---|---|---|
| `detail` | 137.342 – 137.392 | -4.740 – -4.652 | 2 m | Main site: Pahrump Hills → Hematite Hotspot (~3 × 5 km) |
| `route_full` | 137.342 – 137.460 | -4.740 – -4.579 | 3 m | Curiosity traverse Bradbury Landing → Hematite Hotspot |
| `boxwork` | 137.369 – 137.400 | -4.819 – -4.783 | 2 m | Second site: boxwork formations (Curiosity, sols 4500–4800, 2025) |
| `context` | 137.1225 – 137.7298 | -4.9255 – -4.2490 | 25 m | Full coverage of the HiRISE Gale DEM (~36 × 40 km) |

## Files

### Terrain — `gale/dem/` (MRO HiRISE DEM `Gale_DEM_SMG_1m`, JPL Mars Trek)
Float32 TIFF (zlib), **meters**, no missing values in any crop.

| File | Size px | Elevation range |
|---|---|---|
| `dem_detail.tif` | 1479 × 2603 @ 2 m | -4485 to -4029 m |
| `dem_route_full.tif` | 2327 × 3175 @ 3 m | -4531 to -3735 m |
| `dem_boxwork.tif` | 917 × 1065 @ 2 m | -3851 to -3365 m |
| `dem_context.tif` | 1437 × 1601 @ 25 m | -4639 to +359 m |

Why 2 m and not 1 m: the server times out (60 s) at 1 m even for 512 × 512 px requests. Slope should be computed on
a 3–5 m base anyway (1 m HiRISE noise inflates it).

### Imagery — `gale/imagery/`
- `hirise_mosaic_{detail,route_full,boxwork,context}.png` — MRO HiRISE grayscale mosaic, same boxes and pixel size as the DEM crops (pixel-aligned for draping).
- `ctx_mosaic_context.png` — MRO CTX Gale mosaic, ~10 m/px, context box (WMTS zoom 12).

### Layers — `gale/layers/` (context box)
- `themis_ir_day_context.png`, `themis_ir_night_context.png` — Mars Odyssey THEMIS IR controlled mosaics, ~81 m/px. Night IR relates to thermal inertia (rock vs. loose sand/dust). These are rendered images (not calibrated values).
- `geology_sim3292_context.png` — USGS global geologic map SIM 3292 (1:20M). Very coarse: only a couple of units in the area.
- `hydrous_minerals_context.geojson` — OMEGA/CRISM hydrous mineral detections: **0 features inside Gale** in this catalog layer, so it is not usable as a layer.
- Not downloaded: MGS TES thermal inertia (the service only offers ~83 km/px, useless at this scale).

### Rover traverse — `gale/vector/`
- `curiosity_path.geojson`, `curiosity_waypoints.geojson` — Mars Trek. Path ends at Hematite Hotspot (Vera Rubin Ridge); 14 named waypoints, no dates.
- `msl_traverse_jpl.geojson`, `msl_waypoints_jpl.geojson` — **official JPL MSL mission map**, whole mission up to **sol 5073**: 1389 waypoints with sol, lon/lat, elevation, distance driven. Use these to locate stops and to validate routes.

Traverse facts: Bradbury → Hematite Hotspot = 18.8 km driven (8.8 km straight line); Pahrump Hills → Hematite Hotspot = 9.6 km driven.

### Radiation — `rad/` (MSL RAD, NASA SPDF CDAWeb `MSL_RAD_OBS-L2`, variable `DOSIMETRY_TOTAL_f`)
One CSV per year (2012–2026), ~31,000 rows/year (one every ~17 min). Columns: UTC time,
`TOTAL_DOSE_B` (µGy/day in silicon), `RTG_CORRECTED_DOSE_B` (µGy/day, rover generator contribution removed),
`TOTAL_DOSE_E` (µGy/day in tissue-equivalent plastic). Check the `#` header for fill values and quality notes.
- **Absorbed dose, not dose equivalent (mSv).** The conversion needs a published quality factor (to be cited).
- **Gap:** no data from Sep 2012 to Feb 2013 (2012 has 42 rows, 2013 starts in March). Confirmed missing in CDAWeb itself, filtered and unfiltered.

### Weather — `rems/` (MSL REMS MODRDR "RMD", NASA PDS Atmospheres Node)
8 sols spread over one Mars year: 1200, 1280, 1360, 1440, 1520, 1600, 1680, 1760.
- `.TAB` — ASCII table at 1 Hz, **no header**, `-999` = missing. ~12 MB per sol.
- `.xml` — PDS4 label with the column definitions (wind speed/direction, ground and air temperature, UV A/B/C, humidity, pressure, local times LMST/LTST, confidence flags...).

### Point-of-view photos — `photos/` (MSL Navcam raw images, NASA)
56 full-frame Navcam images pointing near the horizon (mast elevation -25° to +10°), up to 4 per stop, spread over
different azimuths: 11 named waypoints from Darwin Outcrop to Hematite Hotspot + 3 boxwork stops (sols 4537, 4602, 4669).
`photos/index.json` gives stop, sol, instrument, date, URL and credit for each image.
- Some images show the rover deck (camera looking back): pick the best ones per stop.
- No horizon-pointing images were found for Bradbury Landing, Rocknest and Glenelg (outside the main site).
- Credit: **NASA/JPL-Caltech**.

## Processed package — `data/processed/gale_radar/` (branch `plan2d`)

Built from the files above with `python scripts/build_package.py configs/packages/gale_radar.yaml` (~15 s). It is
committed to Git (13 MB) so the viewer runs without rebuilding. Uses only `gale/dem/dem_detail.*`,
`gale/imagery/hirise_mosaic_detail.*`, `gale/vector/curiosity_waypoints.geojson`, `gale/vector/msl_waypoints_jpl.geojson`,
`photos/`, `rad/` and `rems/`.

| Folder | Content |
|---|---|
| `terrain/` | Hillshade, HiRISE copy, slope (deg), red hazard mask (> 20°) at 2 m; elevation (uint16) and return time to the pickup (min) at 4 m |
| `route/` | Modelled route through the stops (points with time, distance, slope; KPIs; validation) and Curiosity's track |
| `science/` | 9 named stops with role, timing, photos (`photos/`, downsized, credit NASA/JPL-Caltech) |
| `environment/` | RAD summary (reference rate, gaps, 2024-05-20 event, illustrative budget) and REMS hourly cycles of 8 sols |
| `site.json` | Grids, layers (encoding, units, source, derivation), scenario, assumptions, warnings, sources |
| `preview.png` | Sanity-check image of the layers |

Format details for the viewer: [`web/README.md`](../web/README.md).
