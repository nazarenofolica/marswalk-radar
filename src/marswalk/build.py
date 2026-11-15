"""Build the static package for the radar viewer.

Usage (from the repo root):
    python scripts/build_package.py configs/packages/gale_radar.yaml

Reads only data/raw/ (never modifies it) and writes data/processed/<package id>/.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import tifffile
import yaml
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from . import environment, package, stops as stops_mod, terrain
from .geo import Grid
from .route import RouteGraph

ROOT = Path(__file__).resolve().parents[2]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build(config_path: Path) -> Path:
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    P = {k: ROOT / v for k, v in cfg["inputs"].items()}
    out = ROOT / cfg["package"]["output"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    T, S, E = cfg["terrain"], cfg["scenario"], cfg["environment"]

    # ------------------------------------------------------------------ terrain (2 m grid)
    log("terrain: loading DEM")
    meta = json.loads(P["dem_meta"].read_text(encoding="utf-8"))
    dem = tifffile.imread(P["dem"]).astype(np.float32)
    g2 = Grid.from_sidecar(meta)
    assert dem.shape == (g2.height, g2.width), "DEM shape does not match its sidecar"
    assert np.isfinite(dem).all() and dem.min() > -1e30, "DEM has missing values"
    hir = np.array(Image.open(P["hirise"]).convert("L"))
    assert hir.shape == dem.shape, "HiRISE mosaic is not pixel-aligned with the DEM"

    sigma, thr = T["smoothing_sigma_px"], T["hazard_slope_deg"]
    slope2 = terrain.slope_deg(dem, g2.px_x_m, g2.px_y_m, sigma)
    shade = terrain.hillshade(dem, g2.px_x_m, g2.px_y_m, sigma)
    hazard2 = slope2 > thr
    package.write_gray_png(out / "terrain/hillshade.png", shade)
    package.write_u8_png(out / "terrain/hirise.png", hir)
    package.write_u8_png(out / "terrain/slope_deg.png", np.clip(np.round(slope2), 0, 90))
    package.write_rgba_png(out / "terrain/hazard_slope.png", hazard2, (255, 59, 48), 190)
    log(f"terrain: slope median {np.median(slope2):.1f} deg, {hazard2.mean() * 100:.2f}% above {thr} deg")

    # ------------------------------------------------------------------ routing grid (4 m)
    g4, elev4, slope4 = terrain.routing_grid(dem, slope2, g2, T["routing_block"])
    elev_enc = package.write_u16_bin(out / "terrain/elevation_4m.u16", elev4)
    log(f"routing grid: {g4.width} x {g4.height} cells of {g4.px_x_m:.2f} x {g4.px_y_m:.2f} m")
    graph = RouteGraph(elev4, slope4, g4.px_x_m, g4.px_y_m, thr, S["base_speed_kmh"])

    # ------------------------------------------------------------------ stops
    named = stops_mod.load_named_waypoints(P["waypoints_named"])
    order = [S["start"], *S["stops"], S["pickup"]]
    for n in order:
        if n not in named:
            raise SystemExit(f"stop '{n}' not found in {P['waypoints_named'].name}")
    snapped = {}
    for n in order:
        r, c = g4.lonlat_to_cell(*named[n])
        snapped[n] = graph.nearest_walkable(int(r), int(c))

    # ------------------------------------------------------------------ modelled route through the stops
    log("route: computing legs")
    full, leg_ends, no_path = [], [], None
    for a, b in zip(order[:-1], order[1:]):
        p = graph.shortest_path(snapped[a][:2], snapped[b][:2])
        if p is None:
            no_path = {"from": a, "to": b, "max_slope_deg": thr}
            break
        full.extend(p if not full else p[1:])
        leg_ends.append(len(full) - 1)
    if no_path:
        raise SystemExit(f"No walkable path under {thr} deg between {no_path['from']} and {no_path['to']}")
    check = graph.verify(full)
    seg, seg_t, dz = graph.path_metrics(full)
    dwell = float(S["dwell_min_per_stop"])

    pts, pt_cells, t, d = [], [], 0.0, 0.0
    arrive_idx = {0: order[0]} | {leg_ends[i]: order[i + 1] for i in range(len(leg_ends))}
    stop_times = {}
    for i, (r, c) in enumerate(full):
        if i > 0:
            t += float(seg_t[i - 1]); d += float(seg[i - 1])
        x, y = g4.rowcol_to_xy(r, c); lon, lat = g4.xy_to_lonlat(x, y)
        rec = {"x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(elev4[r, c]), 2),
               "lon": round(float(lon), 7), "lat": round(float(lat), 7),
               "slope_deg": round(float(slope4[r, c]), 1), "dist_m": round(d, 1), "t_min": round(t, 2)}
        name = arrive_idx.get(i)
        if name:
            rec["event"] = "arrive" if name != order[0] else "start"
            rec["stop"] = name
            stop_times[name] = {"arrive_t_min": round(t, 2), "dist_m": round(d, 1)}
        pts.append(rec); pt_cells.append((r, c))
        if name and name not in (order[0], order[-1]):          # dwell at science stops
            t += dwell
            pts.append({**rec, "t_min": round(t, 2), "event": "depart"}); pt_cells.append((r, c))
            stop_times[name]["depart_t_min"] = round(t, 2)

    walk_min = float(seg_t.sum())
    total_min = walk_min + dwell * len(S["stops"])
    eva_min = float(S["eva_duration_h"]) * 60.0

    # ------------------------------------------------------------------ return isochrone (minutes to pickup)
    log("isochrone: minutes to walk from every cell to the pickup point")
    ret = graph.time_to(snapped[order[-1]][:2])
    ret_u8 = np.where(np.isfinite(ret), np.clip(np.round(ret / 2.0), 0, 254), 255).astype(np.uint8)
    package.write_u8_png(out / "terrain/return_time_4m.png", ret_u8)
    rr = np.array([p[0] for p in full]); cc = np.array([p[1] for p in full])
    margin = [eva_min - p["t_min"] - float(ret[r, c]) for p, (r, c) in zip(pts, pt_cells)]

    # ------------------------------------------------------------------ Curiosity track + validation
    track = stops_mod.curiosity_track(P["waypoints_jpl"], g2, dem)
    txy = np.array([[p["x"], p["y"]] for p in track])
    dense = []
    for a, b in zip(txy[:-1], txy[1:]):
        L = float(np.hypot(*(b - a)))
        if L > 200:            # do not draw straight lines across gaps in the waypoint list
            continue
        n = max(2, int(L / 2) + 1)
        dense.append(np.linspace(a, b, n))
    tree = cKDTree(np.vstack(dense))
    rxy = np.array([[p["x"], p["y"]] for p in pts])
    dist_to_track, _ = tree.query(rxy)
    w = np.r_[np.diff([p["dist_m"] for p in pts]), 0.0]
    corridor = float(cfg["validation"]["corridor_m"])
    in_corr = float((w * (dist_to_track <= corridor)).sum() / max(w.sum(), 1e-9))

    def nearest_track(name):
        x, y = g2.lonlat_to_xy(*named[name])
        k = int(np.argmin(np.hypot(txy[:, 0] - x, txy[:, 1] - y)))
        return track[k]
    cur_start, cur_end = nearest_track(order[0]), nearest_track(order[-1])
    curiosity_dist = (cur_end["dist_total_m"] or 0) - (cur_start["dist_total_m"] or 0)

    zs = np.array([p["z"] for p in pts])
    dzs = np.diff(zs)
    slopes_seg = np.maximum(slope4[rr[:-1], cc[:-1]], slope4[rr[1:], cc[1:]])
    bands = {}
    for lo, hi in [(0, 5), (5, 10), (10, 15), (15, thr)]:
        m = (slopes_seg >= lo) & (slopes_seg < hi if hi < thr else slopes_seg <= hi)
        bands[f"{lo}-{hi}"] = round(float(seg[m].sum() / seg.sum()), 3)

    summary = {
        "distance_m": round(float(seg.sum()), 1),
        "straight_line_m": round(float(np.hypot(pts[-1]["x"] - pts[0]["x"], pts[-1]["y"] - pts[0]["y"])), 1),
        "walking_time_min": round(walk_min, 1),
        "dwell_time_min": round(dwell * len(S["stops"]), 1),
        "eva_total_min": round(total_min, 1),
        "eva_budget_min": eva_min,
        "fits_eva_budget": bool(total_min <= eva_min),
        "min_return_margin_min": round(float(min(margin)), 1),
        "elevation_gain_m": round(float(dzs[dzs > 0].sum()), 1),
        "elevation_loss_m": round(float(-dzs[dzs < 0].sum()), 1),
        "start_elevation_m": round(float(zs[0]), 1), "end_elevation_m": round(float(zs[-1]), 1),
        "max_slope_deg": round(float(slope4[rr, cc].max()), 1),
        "slope_bands_share_of_distance": bands,
    }
    validation = {
        "path_check": check,
        "curiosity_corridor_m": corridor,
        "share_within_corridor": round(in_corr, 3),
        "mean_distance_to_curiosity_m": round(float(np.average(dist_to_track, weights=np.maximum(w, 1e-9))), 1),
        "curiosity_driven_m_same_endpoints": round(float(curiosity_dist), 1),
        "note": "Curiosity's track is a historical rover reference, not proof of human traversability.",
    }
    log(f"route: {summary['distance_m'] / 1000:.2f} km, walk {walk_min:.0f} min, EVA {total_min:.0f}/{eva_min:.0f} min, "
        f"{in_corr * 100:.0f}% within {corridor:.0f} m of Curiosity, path check {check}")

    # ------------------------------------------------------------------ photos + stops file
    log("stops: exporting photos")
    in_area = [n for n, (lo, la) in named.items() if g2.contains(lo, la)]
    photos = stops_mod.export_photos(P["photos_index"], ROOT, in_area, out)
    stops_out = []
    for n in in_area:
        lo, la = named[n]
        x, y = g2.lonlat_to_xy(lo, la); r, c = g2.lonlat_to_cell(lo, la)
        role = "start" if n == order[0] else "pickup" if n == order[-1] else "science" if n in S["stops"] else "context"
        near = nearest_track(n)
        stops_out.append({
            "name": n, "id": stops_mod.slug(n), "role": role, "visited": n in order,
            "order": order.index(n) if n in order else None,
            "lon": lo, "lat": la, "x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(dem[r, c]), 2),
            "snap_offset_m": round(snapped[n][2], 1) if n in snapped else None,
            **stop_times.get(n, {}),
            "dwell_min": dwell if role == "science" else 0,
            "curiosity_sol_nearby": near["sol"],
            "science_note": (cfg.get("science_notes") or {}).get(n, {}).get("text", ""),
            "science_source": (cfg.get("science_notes") or {}).get(n, {}).get("source", ""),
            "photos": photos.get(n, []),
        })
    stops_out.sort(key=lambda s: (s["order"] is None, s["order"] if s["order"] is not None else 0, s["name"]))

    # ------------------------------------------------------------------ environment
    log("environment: RAD and REMS")
    rad = environment.radiation_summary(environment.load_rad(P["rad_dir"]), E["rad_reference_window_days"],
                                        float(S["eva_duration_h"]), float(E["rad_alert_factor"]), E["solar_event_date"])
    wx = environment.weather_summary(P["rems_dir"], S["start_lmst"], float(S["eva_duration_h"]))

    # ------------------------------------------------------------------ write files
    package.write_json(out / "route/route.json", {
        "name": "Modelled route (lower terrain difficulty, through the science stops)",
        "label": "Modelled route: estimated, not a guarantee of traversability",
        "stops_order": order, "summary": summary, "validation": validation, "points": pts})
    package.write_json(out / "route/curiosity_track.json", {
        "label": "Curiosity historical track (JPL mission map), reference only", "points": track})
    package.write_json(out / "science/stops.json", stops_out)
    package.write_json(out / "environment/radiation.json", rad)
    package.write_json(out / "environment/weather.json", wx)

    site = {
        "schema_version": package.SCHEMA_VERSION,
        "id": cfg["package"]["id"], "name": cfg["package"]["name"],
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crs": {"body": "Mars", "model": "sphere r = 3389.5 km",
                "local_frame": "x = meters East, y = meters North, origin at the SW corner of the bbox",
                "origin_lonlat": [g2.lon0, g2.lat0]},
        "grids": {"terrain_2m": g2.to_dict(), "routing_4m": g4.to_dict()},
        "layers": {
            "hillshade": {"file": "terrain/hillshade.png", "grid": "terrain_2m", "encoding": "uint8 gray, 0 = dark, 255 = lit",
                          "units": "relative", "source": "derived from MRO HiRISE DEM Gale_DEM_SMG_1m",
                          "derivation": f"Gaussian smoothing sigma={sigma} px, light azimuth 315, altitude 45"},
            "hirise": {"file": "terrain/hirise.png", "grid": "terrain_2m", "encoding": "uint8 gray brightness",
                       "units": "relative", "source": "MRO HiRISE mosaic curiosity_hirise_mosaic (Mars Trek)",
                       "derivation": "copied, pixel-aligned with the DEM"},
            "slope": {"file": "terrain/slope_deg.png", "grid": "terrain_2m", "encoding": "uint8, value = slope in degrees (0-90)",
                      "units": "deg", "source": "derived from the DEM",
                      "derivation": f"np.gradient on DEM smoothed with sigma={sigma} px (effective base ~4-6 m)"},
            "hazard": {"file": "terrain/hazard_slope.png", "grid": "terrain_2m", "encoding": "RGBA, red where slope > threshold",
                       "units": "mask", "threshold_deg": thr, "source": "derived from the DEM",
                       "derivation": f"slope > {thr} deg (assumption)"},
            "elevation": {"file": "terrain/elevation_4m.u16", "grid": "routing_4m", "encoding": elev_enc,
                          "units": "m (areoid-referenced, negative is normal in Gale)",
                          "source": "MRO HiRISE DEM Gale_DEM_SMG_1m", "derivation": "2x2 block mean of the 2 m DEM"},
            "return_time": {"file": "terrain/return_time_4m.png", "grid": "routing_4m",
                            "encoding": "uint8, minutes = 2 x value; 255 = not reachable under the slope limit",
                            "units": "min", "source": "derived (route engine)",
                            "derivation": "Dijkstra on the reverse graph from the pickup point; same speed model and slope limit as the route"},
        },
        "files": {"route": "route/route.json", "curiosity_track": "route/curiosity_track.json",
                  "stops": "science/stops.json", "radiation": "environment/radiation.json",
                  "weather": "environment/weather.json"},
        "scenario": {**S, "hazard_slope_deg": thr},
        "assumptions": [
            f"Walking speed: Tobler-type hiking curve scaled to {S['base_speed_kmh']} km/h on flat ground. Exploratory, not validated for suited astronauts on Mars.",
            f"Cells steeper than {thr} deg are not walkable (hard limit).",
            f"{S['dwell_min_per_stop']} min at each science stop; EVA budget {S['eva_duration_h']} h; crew of {S['crew']}; pressurized rover pickup at {S['pickup']}.",
            "Astronaut position in the viewer is simulated along the modelled route (in a real EVA it would come from inertial/visual odometry).",
            f"Radiation alert at {E['rad_alert_factor']} x the reference rate is illustrative.",
            f"Scout robot: drives the modelled route ahead of the crew at {S.get('robot_speed_kmh', 1.0)} km/h, assumed for known terrain (Curiosity's top speed is about 0.15 km/h).",
        ],
        "warnings": [
            "All environmental data are historical observations, not live or forecast values.",
            "RAD values are absorbed dose rates measured by the rover, not human dose (no mSv conversion).",
            "Slope is computed from a 2 m DEM; obstacles smaller than ~4-6 m (single rocks) are not resolved.",
            "HiRISE brightness is NOT used to detect sand: tested on the Bagnold Dunes stops, it does not separate sand from rock.",
            "Curiosity's track is a rover reference, not proof of human traversability.",
        ],
        "sources": [
            {"data": "HiRISE DEM Gale_DEM_SMG_1m, HiRISE and CTX mosaics", "provider": "NASA JPL Mars Trek", "downloaded": "2026-11-14"},
            {"data": "Curiosity waypoints and traverse (MMGIS)", "provider": "NASA JPL MSL mission map", "downloaded": "2026-11-14"},
            {"data": "MSL RAD MSL_RAD_OBS-L2", "provider": "NASA SPDF CDAWeb", "downloaded": "2026-11-14"},
            {"data": "MSL REMS MODRDR", "provider": "NASA PDS Atmospheres Node", "downloaded": "2026-11-14"},
            {"data": "MSL Navcam raw images", "provider": "NASA/JPL-Caltech", "downloaded": "2026-11-14"},
        ],
    }
    package.write_json(out / "site.json", site)

    # ------------------------------------------------------------------ preview (sanity check image)
    prev = Image.fromarray(np.round(shade * 200).astype(np.uint8)).convert("RGB")
    prev.paste(Image.new("RGB", prev.size, (255, 59, 48)), mask=Image.fromarray((hazard2 * 170).astype(np.uint8)))
    dr = ImageDraw.Draw(prev)
    to_px = lambda x, y: (x / g2.px_x_m, g2.height - y / g2.px_y_m)  # noqa: E731
    seg_pts = []
    for p in track + [{"x": 0, "y": 0, "break_before": True}]:
        if p["break_before"] and len(seg_pts) > 1:
            dr.line(seg_pts, fill=(235, 235, 235), width=2)
        seg_pts = [to_px(p["x"], p["y"])] if p["break_before"] else seg_pts + [to_px(p["x"], p["y"])]
    dr.line([to_px(p["x"], p["y"]) for p in pts], fill=(52, 199, 89), width=5)
    for s in stops_out:
        u, v = to_px(s["x"], s["y"]); rad_px = 11 if s["visited"] else 7
        dr.ellipse([u - rad_px, v - rad_px, u + rad_px, v + rad_px], outline=(0, 229, 255), width=4)
    prev.thumbnail((900, 1600))
    prev.save(out / "preview.png")

    problems = package.validate(out)
    if problems:
        raise SystemExit("package invalid:\n  " + "\n  ".join(problems))
    log(f"package OK -> {out.relative_to(ROOT)}")
    return out


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python scripts/build_package.py configs/packages/<package>.yaml")
    build(Path(argv[1]))


if __name__ == "__main__":
    main(sys.argv)
