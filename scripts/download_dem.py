"""Descarga los recortes del DEM de Gale (HiRISE, 1 m) desde Mars Trek.

Pide al ImageServer `exportImage` en float32 (metros), parte los pedidos grandes por el límite
de alto del servidor, los une y guarda un TIFF comprimido + un JSON con metadatos.

Salida: data/raw/gale/dem/dem_<nombre>.tif y dem_<nombre>.json   (nombre = detail | route_full | context)
Uso:    python scripts/download_dem.py [detail route_full context]
"""
from __future__ import annotations

import io
import json
import sys

import numpy as np
import tifffile

import common as c

NODATA_LIMIT = -1e30


def tile_px(pixel_m: float) -> int:
    """Tamaño de bloque por pedido. El servidor corta a los 60 s y lee más lento cuanto más fina la resolución:
    a 2 m, 1024 px tardó 48 s (al borde) -> usamos 512; a 3 m o más, 2048 px tarda ~12 s."""
    return 512 if pixel_m < 3 else 2048


def fetch_tile(s, endpoint, lon, lat, w, h) -> np.ndarray:
    params = {
        "bbox": f"{lon[0]},{lat[0]},{lon[1]},{lat[1]}",
        "bboxSR": 4326,
        "imageSR": 4326,
        "size": f"{w},{h}",
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    r = c.get(s, f"{endpoint}/exportImage", params=params)
    arr = tifffile.imread(io.BytesIO(r.content))
    if arr.shape != (h, w):
        raise RuntimeError(f"Tamaño inesperado {arr.shape}, se pidió {(h, w)}")
    return arr.astype("float32")


def download(name: str, bbox: dict, endpoint: str) -> None:
    lon, lat, pm = bbox["lon"], bbox["lat"], bbox["pixel_m"]
    w, h = c.bbox_size_px(lon, lat, pm)
    t = tile_px(pm)
    nx, ny = -(-w // t), -(-h // t)
    print(f"[{name}] {w}x{h}px a {pm} m/px, {nx}x{ny} bloques de hasta {t}px")
    s = c.session()
    dlon = (lon[1] - lon[0]) / w  # grados por columna
    dlat = (lat[1] - lat[0]) / h  # grados por fila
    full = np.empty((h, w), dtype="float32")
    done = 0
    for j in range(ny):
        r0, r1 = j * t, min((j + 1) * t, h)
        top = lat[1] - r0 * dlat  # fila 0 = borde norte (lat max)
        for i in range(nx):
            c0, c1 = i * t, min((i + 1) * t, w)
            left = lon[0] + c0 * dlon
            full[r0:r1, c0:c1] = fetch_tile(
                s, endpoint, [left, left + (c1 - c0) * dlon], [top - (r1 - r0) * dlat, top], c1 - c0, r1 - r0
            )
            done += 1
            print(f"  bloque {done}/{nx * ny} ok", flush=True)
    valid = np.isfinite(full) & (full > NODATA_LIMIT)
    nodata_pct = 100 * (1 - valid.mean())
    if not valid.any():
        raise RuntimeError(f"{name}: no hay datos válidos")
    vmin, vmax = float(full[valid].min()), float(full[valid].max())
    out_dir = c.DATA_RAW / "gale" / "dem"
    out_dir.mkdir(parents=True, exist_ok=True)
    tif = out_dir / f"dem_{name}.tif"
    tifffile.imwrite(tif, full, compression="zlib")
    meta = {
        "name": name,
        "units": "meters (elevación sobre datum de Marte, float32)",
        "crs": "EPSG:4326-like, grilla geográfica de Marte (esfera r=3389,5 km)",
        "bbox_lon": lon,
        "bbox_lat": lat,
        "width_px": w,
        "height_px": h,
        "pixel_deg_x": (lon[1] - lon[0]) / w,
        "pixel_deg_y": (lat[1] - lat[0]) / h,
        "pixel_m_nominal": pm,
        "row0_is": "north",
        "min_m": vmin,
        "max_m": vmax,
        "nodata_percent": round(nodata_pct, 4),
        "nodata_rule": f"NaN o valor < {NODATA_LIMIT}",
    }
    (out_dir / f"dem_{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  -> {tif.name}: {tif.stat().st_size / 1e6:.1f} MB, rango {vmin:.1f}..{vmax:.1f} m, sin dato {nodata_pct:.3f}%")
    c.record(
        tif,
        source=f"{endpoint}/exportImage (Mars Trek, JPL/NASA; HiRISE DEM, producto MRO HiRISE Gale Crater DEM)",
        description=f"DEM Gale recorte '{name}' a {pm} m/px",
        extra={"min_m": vmin, "max_m": vmax},
    )


def main() -> None:
    cfg = c.load_config()
    endpoint = f"{cfg['trek']['arcgis']}/{cfg['trek']['services']['dem']}/ImageServer"
    names = sys.argv[1:] or list(cfg["bboxes"])
    for name in names:
        download(name, cfg["bboxes"][name], endpoint)


if __name__ == "__main__":
    main()
