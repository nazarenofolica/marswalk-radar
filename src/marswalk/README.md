# src/marswalk — processing pipeline (plan 2D)

Turns `data/raw/` into the static package `data/processed/gale_radar/` read by the viewer.

```bash
python scripts/build_package.py configs/packages/gale_radar.yaml   # ~15 s
python -m pytest -q
```

| Module | What it does |
|---|---|
| `geo.py` | `Grid`: lon/lat ↔ local meters (x East, y North, origin SW corner) ↔ raster row/col. Spherical Mars, r = 3389.5 km |
| `terrain.py` | Slope (deg) on the smoothed DEM, hillshade, 2×2 aggregation to the 4 m routing grid (max slope, mean elevation) |
| `route.py` | `RouteGraph`: 8-neighbour directed graph, hard slope limit, no corner cutting, Tobler-type walking time; shortest path, time-to-target field (return isochrone), post-check |
| `stops.py` | Named stops, Curiosity's track inside the bbox, downsized Navcam photos |
| `environment.py` | RAD summary (reference rate, gaps, 2024-05-20 event, illustrative budget) and REMS hourly cycles (confidence flags applied) |
| `package.py` | PNG / uint16 / JSON writers and the package validator |
| `build.py` | Reads the YAML, runs everything, writes `site.json`, `preview.png`, and validates |

The output format (layers, encodings, coordinates, files) is documented for the viewer in
[`web/README.md`](../../web/README.md). `site.json` repeats it in machine-readable form.

To change the scenario (stops, speed, slope limit, EVA duration, start time, science texts), edit
`configs/packages/gale_radar.yaml` and rebuild. Never edit `data/processed/` by hand.
