"""Named science stops (Mars Trek waypoints), Curiosity's historical track and point-of-view photos."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .geo import Grid


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_named_waypoints(path: Path) -> dict[str, tuple[float, float]]:
    feats = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    return {f["properties"]["name"]: tuple(f["geometry"]["coordinates"][:2]) for f in feats}


def curiosity_track(path: Path, grid: Grid, dem: np.ndarray) -> list[dict]:
    """JPL waypoints inside the bbox, ordered by sol, as local meters (+ DEM elevation)."""
    feats = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    pts = sorted((f["properties"] for f in feats), key=lambda p: (p["sol"], p.get("dist_total_m", 0)))
    out = []
    for p in pts:
        if not grid.contains(p["lon"], p["lat"]):
            continue
        x, y = grid.lonlat_to_xy(p["lon"], p["lat"])
        r, c = grid.lonlat_to_cell(p["lon"], p["lat"])
        # break_before: do not draw a line from the previous point (gap > 200 m, e.g. the rover left the bbox)
        brk = not out or float(np.hypot(x - out[-1]["x"], y - out[-1]["y"])) > 200
        out.append({"sol": int(p["sol"]), "lon": round(p["lon"], 7), "lat": round(p["lat"], 7),
                    "x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(dem[r, c]), 2),
                    "z_jpl": p.get("elev_geoid"), "dist_total_m": p.get("dist_total_m"), "break_before": brk})
    return out


def export_photos(index_path: Path, root: Path, stop_names: list[str], out_dir: Path, max_px: int = 768) -> dict:
    """Copy and downsize the photos of each stop. Returns {stop name: [photo records]}.

    The raw index stores up to 4 Navcam frames per stop; the team picks the best ones later
    (some frames show the rover deck). Order is kept as in the index.
    """
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    res: dict[str, list] = {n: [] for n in stop_names}
    for rec in index:
        if rec["stop"] not in res:
            continue
        src = root / rec["file"]
        if not src.exists():
            continue
        dst_rel = Path("photos") / slug(rec["stop"]) / (Path(rec["file"]).stem + ".jpg")
        dst = out_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("L")
        im.thumbnail((max_px, max_px))
        im.save(dst, quality=84, optimize=True)
        res[rec["stop"]].append({"file": dst_rel.as_posix(), "sol": rec["sol"], "date_utc": rec["date_taken"][:10],
                                 "instrument": rec["instrument"], "credit": rec["credit"], "source_url": rec["url"],
                                 "stop_match_error_m": rec.get("stop_match_error_m")})
    return res
