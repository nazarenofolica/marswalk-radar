"""Writers for the static package read by the viewer, and the package validator."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

SCHEMA_VERSION = "radar-1.0"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def write_gray_png(path: Path, a01: np.ndarray) -> None:
    """Float array in [0, 1] -> 8-bit grayscale PNG (row 0 = top = north)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(np.round(a01 * 255), 0, 255).astype(np.uint8), "L").save(path, optimize=True)


def write_u8_png(path: Path, a: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a.astype(np.uint8), "L").save(path, optimize=True)


def write_rgba_png(path: Path, mask: np.ndarray, rgb: tuple[int, int, int], alpha: int) -> None:
    """Transparent PNG: `rgb` with `alpha` where mask is True, fully transparent elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = np.zeros(mask.shape + (4,), np.uint8)
    out[mask] = (*rgb, alpha)
    Image.fromarray(out, "RGBA").save(path, optimize=True)


def write_u16_bin(path: Path, values: np.ndarray) -> dict:
    """Raw little-endian uint16 (row-major, row 0 = north). value_m = offset + scale * u16. Returns encoding."""
    v = values.astype(np.float64)
    lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
    scale = (hi - lo) / 65535.0 if hi > lo else 1.0
    q = np.round((v - lo) / scale).astype("<u2")
    path.parent.mkdir(parents=True, exist_ok=True)
    q.tofile(path)
    return {"dtype": "uint16", "byte_order": "little", "order": "row-major, row 0 = north",
            "offset": lo, "scale": scale, "min": lo, "max": hi, "max_quantization_error": scale / 2}


def validate(pkg_dir: Path) -> list[str]:
    """Return a list of problems (empty list = valid package)."""
    pkg_dir = Path(pkg_dir)
    problems = []
    site_path = pkg_dir / "site.json"
    if not site_path.exists():
        return ["site.json missing"]
    site = json.loads(site_path.read_text(encoding="utf-8"))
    if site.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version {site.get('schema_version')} != {SCHEMA_VERSION}")
    grids = site.get("grids", {})
    for name, layer in site.get("layers", {}).items():
        f = pkg_dir / layer["file"]
        if not f.exists():
            problems.append(f"layer {name}: missing {layer['file']}"); continue
        g = grids[layer["grid"]]
        if f.suffix == ".png":
            w, h = Image.open(f).size
            if (w, h) != (g["width_px"], g["height_px"]):
                problems.append(f"layer {name}: size {w}x{h} != grid {layer['grid']} {g['width_px']}x{g['height_px']}")
        elif f.suffix == ".bin":
            n = f.stat().st_size // 2
            if n != g["width_px"] * g["height_px"]:
                problems.append(f"layer {name}: {n} values != {g['width_px']}x{g['height_px']}")
    for key, rel in site.get("files", {}).items():
        if not (pkg_dir / rel).exists():
            problems.append(f"file {key}: missing {rel}")
    stops = json.loads((pkg_dir / site["files"]["stops"]).read_text(encoding="utf-8")) if "stops" in site.get("files", {}) else []
    for s in stops:
        for p in s.get("photos", []):
            if not (pkg_dir / p["file"]).exists():
                problems.append(f"stop {s['name']}: missing photo {p['file']}")
    return problems
