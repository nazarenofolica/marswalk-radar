# web — Marswalk Radar viewer (three.js)

Static tactical-tablet viewer for a **simulated** Marswalk at Gale Crater. It reads only the package in
`data/processed/gale_radar/` (no external requests, no backend) and runs from any static server.

> Status: **simplified POC** (`index.html`, `style.css`, `main.js`, `view3d.js`). Two entities move along the
> modelled route: a **scout robot** and the **crew** behind it. Three views share one mission clock:
>
> - **Radar** (full screen, crew at the centre, heading up) and **Overview** (whole area, north up): Layers legend,
>   a TERRAIN card (slope, max slope in the next 200 m, elevation) and a WINDOW card (elapsed, remaining, return
>   margin). Each stop marker opens a pop-up with distance by route, ETA, Δz and the Navcam photo.
> - **3D**: first person through a helmet visor, with the same TERRAIN and WINDOW cards, a RADIATION card and a
>   simulation panel that triggers spoken alerts.

## Scout robot

- Speed: `scenario.robot_speed_kmh` in the YAML (1 km/h, an assumption for known terrain; Curiosity's top speed is
  about 0.15 km/h). Constant along the route, no time at the stops.
- Departure: computed in `main.js` so that the robot stays at least 500 m ahead of the crew (`ROBOT_MIN_GAP_M`) on
  the whole route, rounded up to 10 min. With the default scenario it leaves **90 min** before the crew.
- Role: it is the source of the "now" conditions shown to the crew. In this POC those values are set by hand.

## 3D view (`view3d.js` + HTML overlay)

Built the first time the **3D** button is used (it loads `terrain/elevation_4m.u16` and `terrain/hirise.png`).
Terrain mesh from `elevation` (4 m grid, vertical scale 1:1) in 64 × 64 cell tiles with four levels of detail by
distance. The surface colour is a stylised tint of the grayscale `hirise` image, not measured colour, and the
lighting does not follow the time of day. At 4 m per cell the ground next to the camera looks smooth.

- **Visor:** CSS frame over the canvas (rounded glass, nose bridge, faint glare, centre mark).
- **RADIATION card:** `Avg`, `Min` and `Max` come from `radiation.json › climatology`: hourly mean dose rates of
  today's calendar day over every year of RAD data (2012–2026), in the three hours around the simulated time of
  day. RAD timestamps are **UTC**, not Mars local time, so the hour match is a simplification. `Now · robot` is the
  value set in the simulation panel (it follows `Avg` until it is moved).
- **Simulation panel** (button *Simulation*): six conditions set by hand. Crossing a limit shows a red chip and
  plays a chime plus a spoken message (Web Speech API, only with a voice installed on the device, so no request
  leaves the page). Limits are illustrative assumptions of the viewer unless noted:

| Condition | Range | Alert |
|---|---|---|
| Oxygen | 0–100 % | below 20 % |
| CO₂ | 0–15 mmHg | above 7.6 mmHg |
| Suit pressure | 0–40 kPa (default 29.6) | below 25 kPa |
| Atmospheric pressure | 550–1050 Pa | outside the range REMS measured in the package sols (690–945 Pa) |
| Radiation | lowest to highest hourly mean RAD ever measured (126–7826 µGy/day, log scale) | above `radiation.json › alert.threshold` (3 × reference) |
| Time of day | 00:00–23:45 LMST (follows the clock until it is moved) | outside `weather.json › eva_window_lmst` |

The interface says "simulated", never "real-time" or "live" (`PLAN.md` §7).

## URL parameters (demo and testing)

| Parameter | Values | Default |
|---|---|---|
| `t` | mission minute to start at; the crew leaves at `0`, the robot at `-90` | `-90` |
| `play` | `0` starts paused | playing |
| `speed` | `10`, `30`, `60`, `300` | `60` |
| `range` | `250`, `500`, `1000` (m, centre to the top edge) | `250` |
| `view` | `radar`, `overview`, `3d` | `radar` |
| `panel` | `info` opens Assumptions & sources | closed |

Example: `http://localhost:8000/web/?t=262&play=0&view=overview` shows the amber return limit.
Keyboard: space plays or pauses, Esc closes a panel or the stop pop-up. In 3D, drag to look around and double-click
to face the route again.

## Run locally

```bash
python scripts/build_package.py configs/packages/gale_radar.yaml   # only if data/processed/ is missing or the YAML changed
python -m http.server 8000                                          # from the repo ROOT (not from web/)
# open http://localhost:8000/web/
```

The page must be served from the repo root so that `../data/processed/gale_radar/...` resolves (also on GitHub Pages).

## three.js (vendored, r187)

Use an import map in `web/index.html` — never a CDN:

```html
<script type="importmap">
{ "imports": { "three": "./vendor/three.module.js", "three/addons/": "./vendor/addons/" } }
</script>
```

Available addons: `controls/OrbitControls.js`, `controls/MapControls.js`, `renderers/CSS2DRenderer.js`,
`lines/Line2.js`, `lines/LineMaterial.js`, `lines/LineGeometry.js` (thick lines), `lines/LineSegments2.js`,
`lines/LineSegmentsGeometry.js`. Plain ES modules, no build step, no npm.

## Package contract — `data/processed/gale_radar/`

Start from `site.json`: it lists every layer and file with its encoding, units, source and derivation.

### Coordinates (identical for every file)
- Local frame in **meters**: `x` = East, `y` = North, origin = SW corner of the bbox. `site.json › crs`.
- Area: `grids.terrain_2m.width_m` × `height_m` = **2948 × 5206 m**.
- Rasters: row 0 = **north**, col 0 = west. Two grids:
  - `terrain_2m`: 1479 × 2603 px, ~1.993 × 2.000 m per px.
  - `routing_4m`: 740 × 1302 px, ~3.98 × 4.00 m per px (same bbox).
- Raster sample at (x, y): `col = floor(x / px_x_m)`, `row = floor(height_px - y / px_y_m)`.
- Draw a raster as a plane `width_m × height_m` centred at `(width_m/2, height_m/2)`. With `TextureLoader`'s
  default `flipY = true`, the image top lands at +y (north). Data textures (slope, return time): `NearestFilter`,
  no mipmaps, `colorSpace = NoColorSpace`.

### Layers (`site.json › layers`)
| Key | File | Grid | Encoding |
|---|---|---|---|
| `hillshade` | `terrain/hillshade.png` | 2 m | uint8 gray relief (light from NW). Radar base, tint it dark |
| `hirise` | `terrain/hirise.png` | 2 m | uint8 gray orbital photo (surface texture of the 3D view) |
| `slope` | `terrain/slope_deg.png` | 2 m | uint8, value = slope in degrees |
| `hazard` | `terrain/hazard_slope.png` | 2 m | RGBA, red where slope > 20°, transparent elsewhere |
| `elevation` | `terrain/elevation_4m.u16` | 4 m | raw uint16 little-endian; `z_m = offset + scale * v` (see `encoding`) |
| `return_time` | `terrain/return_time_4m.png` | 4 m | uint8; **minutes to walk to the pickup = 2 × value**; 255 = unreachable |

### Files (`site.json › files`)
- `route/route.json` — the modelled route.
  - `points[]`: `x, y, z, lon, lat, slope_deg, dist_m, t_min` (simulation time in minutes since EVA start).
  - Points with `event: "start" | "arrive" | "depart"` and `stop: <name>`. At each science stop there are two points
    with the same position (`arrive`, `depart`) → interpolating position by `t_min` automatically pauses there.
  - `summary`: KPIs (distance, walking/dwell/total time, EVA budget, gain/loss, max slope, slope bands, return margin).
  - `validation`: path check (no blocked cell), share of the route within 50 m of Curiosity's track.
- `route/curiosity_track.json` — Curiosity's historical track inside the area: `points[]` with `x, y, z, sol`.
  Start a new polyline where `break_before` is true.
- `science/stops.json` — 9 named stops. `role`: `start | science | pickup | context`; `visited`, `order`,
  `x, y, z`, `arrive_t_min`, `depart_t_min`, `dist_m` (along the route), `photos[]` (`file`, `sol`, `date_utc`,
  `credit`), `science_note` (**empty until the team writes it** → show "Objective: pending").
- `environment/radiation.json` — RAD: `climatology` (hourly means by calendar day `MM-DD` and UTC hour over all
  years: `by_day[day].mean|min|max|n[hour]`, plus `hourly_range`), `reference.value` (µGy/day, with dates), `illustrative_budget`, `alert`
  (threshold), `annual_median`, `monthly_median[]`, `gaps[]`, `solar_event` (`hourly[]` for the 2024-05-20 replay,
  `baseline_before`, `peak_value`, `worst_eva_window`).
- `environment/weather.json` — REMS: 8 sols, each with `hourly[]` (`hour` LMST, `air_c`, `ground_c`,
  `pressure_pa`, `uv_w_m2`, `rh_pct`), ranges and `eva_window_lmst`. Wind: not available.
- `site.json › scenario`, `assumptions[]`, `warnings[]`, `sources[]` — show them in an "Assumptions & sources" panel.
- `preview.png` — static image of the layers + route + track + stops (sanity check, not for the UI).

## Simulation model (in the browser)
- Clock `t` (minutes) from 0 to `route.summary.eva_total_min`. Play / pause / speed (e.g. ×60, ×300) / scrubber.
- Astronaut position = linear interpolation of `route.points` by `t_min`. Heading = direction to the point ~10 m ahead.
- `remaining = scenario.eva_duration_h * 60 − t`.
- Return margin at the current position = `remaining − return_time(x, y)` (minutes).
- LMST shown = `scenario.start_lmst + t`.
- Radiation replay (toggle): map `solar_event.hourly` onto the EVA clock (the event peak a few hours into the EVA);
  show the alert when the rate exceeds `alert.threshold`. Default (toggle off): constant `reference.value`.

## Drawing the amber return ring
In the terrain fragment shader, sample `return_time` (WebGL gives the byte normalized to 0–1):
`minutes = texel.r * 510.0`; `texel.r > 0.998` means 255 = unreachable (skip it).
- amber line where `abs(minutes − remaining) < ~1.5`;
- optional faint amber veil where `minutes > remaining` (outside the return range).
The ring is not a circle: it follows the terrain because it comes from the same route engine.

## Rules for the interface (from `documentos/PLAN.md`)
- English UI. Words to use: *modelled route*, *estimated time*, *terrain difficulty*, *simulated EVA*, *historical*.
  Never: *safe route*, *safe*, *real-time*, *live*, *AI-powered*.
- Every number shows its unit; every layer has a legend; data say if they are measured, derived or assumed.
- RAD: show "Measured by Curiosity RAD" (with date window), "Illustrative EVA budget" and "Actual astronaut exposure:
  not modelled". No mSv.
- REMS: "Historical REMS observations at Gale Crater (sol N, date)". Never "current weather".
- Photo credit always visible: NASA/JPL-Caltech.
