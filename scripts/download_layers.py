"""Descarga las capas de imagen y mapa de Gale desde Mars Trek.

1. Mosaico HiRISE (curiosity_hirise_mosaic, 8 bits, gris) por `exportImage`, para los 3 recortes.
2. Capas WMTS (CTX, THEMIS día/noche, geología global) unidas y recortadas a la caja `context`.
3. Detecciones de minerales hidratados (CRISM/OMEGA) dentro de la caja `context`, en GeoJSON.

TES inercia térmica se descarta: el servicio solo tiene 2 niveles de zoom (~83 km/px), inútil a esta escala.

Salida: data/raw/gale/imagery/*.png (+ .json con bbox y resolución) y data/raw/gale/layers/*
Uso:    python scripts/download_layers.py
"""
from __future__ import annotations

import io
import json
import math
import sys

import numpy as np
from PIL import Image

import common as c

Image.MAX_IMAGE_PIXELS = None

WMTS_LAYERS = {
    # nombre -> (url con {z}{r}{c}, zoom máximo disponible verificado, descripción, carpeta)
    "ctx_mosaic": (
        "https://trek.nasa.gov/tiles/Mars/EQ/Gale_CTX_BlockAdj_dd/1.0.0//default/default028mm/{z}/{r}/{c}.png",
        12, "MRO CTX, Gale Crater Mosaic (~10 m/px)", "imagery",
    ),
    "themis_ir_night": (
        "https://trek.nasa.gov/tiles/Mars/EQ/THEMIS_NightIR_ControlledMosaics_100m_v2_oct2018/1.0.0//default/default028mm/{z}/{r}/{c}.png",
        9, "Mars Odyssey THEMIS-IR noche, mosaico controlado 100 m (~80 m/px en el zoom 9)", "layers",
    ),
    "themis_ir_day": (
        "https://trek.nasa.gov/tiles/Mars/EQ/THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018/1.0.0//default/default028mm/{z}/{r}/{c}.png",
        9, "Mars Odyssey THEMIS-IR día, mosaico controlado 100 m (~80 m/px en el zoom 9)", "layers",
    ),
    "geology_sim3292": (
        "https://trek.nasa.gov/mars/trekarcgis/rest/services/SIM3292_Global_Geology/MapServer/WMTS/tile/1.0.0/SIM3292_Global_Geology/default/default028mm/{z}/{r}/{c}.png",
        11, "Mapa geológico global de Marte USGS SIM 3292 (escala 1:20M, ~20 m/px en el zoom 11)", "layers",
    ),
}


# ---------- 1. HiRISE por exportImage ----------
def hirise(cfg: dict, names: list[str] | None = None) -> None:
    s = c.session()
    endpoint = f"{cfg['trek']['arcgis']}/{cfg['trek']['services']['hirise_mosaic']}/ImageServer/exportImage"
    out = c.DATA_RAW / "gale" / "imagery"
    out.mkdir(parents=True, exist_ok=True)
    for name, bb in cfg["bboxes"].items():
        if names and name not in names:
            continue
        lon, lat, pm = bb["lon"], bb["lat"], bb["pixel_m"]
        w, h = c.bbox_size_px(lon, lat, pm)
        t = 512 if pm < 3 else 2048  # mismo límite de 60 s por pedido que el DEM
        nx, ny = -(-w // t), -(-h // t)
        print(f"[hirise_{name}] {w}x{h}px, {nx}x{ny} bloques")
        dlon, dlat = (lon[1] - lon[0]) / w, (lat[1] - lat[0]) / h
        arr = np.zeros((h, w), dtype="uint8")
        for j in range(ny):
            r0, r1 = j * t, min((j + 1) * t, h)
            top = lat[1] - r0 * dlat
            for i in range(nx):
                c0_, c1_ = i * t, min((i + 1) * t, w)
                left = lon[0] + c0_ * dlon
                params = {
                    "bbox": f"{left},{top - (r1 - r0) * dlat},{left + (c1_ - c0_) * dlon},{top}",
                    "bboxSR": 4326, "imageSR": 4326, "size": f"{c1_ - c0_},{r1 - r0}",
                    "format": "png", "pixelType": "U8", "interpolation": "RSP_BilinearInterpolation", "f": "image",
                }
                r = c.get(s, endpoint, params=params)
                im = Image.open(io.BytesIO(r.content)).convert("L")
                if im.size != (c1_ - c0_, r1 - r0):
                    raise RuntimeError(f"Tamaño inesperado {im.size}")
                arr[r0:r1, c0_:c1_] = np.asarray(im)
        blank = float((arr == 0).mean()) * 100
        path = out / f"hirise_mosaic_{name}.png"
        Image.fromarray(arr).save(path, optimize=True)
        meta = {
            "name": f"hirise_mosaic_{name}", "bbox_lon": lon, "bbox_lat": lat, "width_px": w, "height_px": h,
            "pixel_m_nominal": pm, "row0_is": "north", "bands": "1 (gris, 8 bits)", "zero_percent": round(blank, 3),
        }
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"  -> {path.name}: {path.stat().st_size / 1e6:.1f} MB, píxeles en 0 (posible sin dato): {blank:.2f}%")
        c.record(path, source=f"{endpoint} (Mars Trek, JPL/NASA; MRO HiRISE Curiosity Roving Site Mosaic)",
                 description=f"Mosaico HiRISE gris, recorte '{name}' a {pm} m/px")


# ---------- 2. WMTS unido y recortado ----------
def wmts(name: str, cfg: dict) -> None:
    template, zoom, desc, folder = WMTS_LAYERS[name]
    bb = cfg["bboxes"]["context"]
    lon, lat = bb["lon"], bb["lat"]
    tile_deg = 180.0 / 2 ** zoom
    deg_px = tile_deg / 256
    c0, c1 = int((lon[0] + 180) // tile_deg), int((lon[1] + 180) // tile_deg)
    r0, r1 = int((90 - lat[1]) // tile_deg), int((90 - lat[0]) // tile_deg)
    ncols, nrows = c1 - c0 + 1, r1 - r0 + 1
    print(f"[{name}] zoom {zoom}: {ncols}x{nrows} tiles ({deg_px * c.KM_PER_DEG * 1000:.1f} m/px)")
    s = c.session()
    canvas = Image.new("RGBA", (ncols * 256, nrows * 256))
    for ri in range(nrows):
        for ci in range(ncols):
            url = template.format(z=zoom, r=r0 + ri, c=c0 + ci)
            resp = c.get(s, url, timeout=60)
            canvas.paste(Image.open(io.BytesIO(resp.content)).convert("RGBA"), (ci * 256, ri * 256))
    # píxeles globales de la caja, relativos al lienzo
    x0 = (lon[0] + 180) / deg_px - c0 * 256
    x1 = (lon[1] + 180) / deg_px - c0 * 256
    y0 = (90 - lat[1]) / deg_px - r0 * 256
    y1 = (90 - lat[0]) / deg_px - r0 * 256
    box = (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))
    crop = canvas.crop(box)
    # bbox real del recorte (ya alineado a píxeles)
    real_lon = [(c0 * 256 + box[0]) * deg_px - 180, (c0 * 256 + box[2]) * deg_px - 180]
    real_lat = [90 - (r0 * 256 + box[3]) * deg_px, 90 - (r0 * 256 + box[1]) * deg_px]
    out = c.DATA_RAW / "gale" / folder
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}_context.png"
    crop.save(path, optimize=True)
    meta = {
        "name": f"{name}_context", "description": desc, "wmts_zoom": zoom,
        "bbox_lon": real_lon, "bbox_lat": real_lat, "width_px": crop.size[0], "height_px": crop.size[1],
        "pixel_deg": deg_px, "pixel_m_nominal": round(deg_px * c.KM_PER_DEG * 1000, 2), "row0_is": "north",
        "mode": "RGBA",
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  -> {path.name}: {crop.size[0]}x{crop.size[1]}, {path.stat().st_size / 1e6:.2f} MB")
    c.record(path, source=template.split("{z}")[0] + " (Mars Trek WMTS, JPL/NASA/USGS)", description=desc)


# ---------- 3. CRISM / OMEGA minerales hidratados ----------
def hydrous(cfg: dict) -> None:
    bb = cfg["bboxes"]["context"]
    url = ("https://trek.nasa.gov/mars/trekarcgis/rest/services/"
           "Hydrous_Mineral_Detections_OMEGA_CRISM_clon0/MapServer/0/query")
    s = c.session()
    params = {
        "geometry": f"{bb['lon'][0]},{bb['lat'][0]},{bb['lon'][1]},{bb['lat'][1]}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": 4326, "f": "json",
    }
    data = c.get(s, url, params=params).json()
    feats = []
    for f in data.get("features", []):
        g = f.get("geometry") or {}
        if "x" in g:
            geom = {"type": "Point", "coordinates": [g["x"], g["y"]]}
        elif "rings" in g:
            geom = {"type": "Polygon", "coordinates": g["rings"]}
        elif "paths" in g:
            geom = {"type": "MultiLineString", "coordinates": g["paths"]}
        else:
            continue
        feats.append({"type": "Feature", "properties": f["attributes"], "geometry": geom})
    out = c.DATA_RAW / "gale" / "layers"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "hydrous_minerals_context.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    kinds = sorted({k for f in feats for k in [f["geometry"]["type"]]})
    print(f"[hydrous] {len(feats)} detección(es) en la caja de contexto, geometrías: {kinds}, campos: {list(data.get('fields') and [x['name'] for x in data['fields']] or [])[:10]}")
    c.record(path, source=url + " (Mars Trek, JPL/NASA; OMEGA y CRISM)",
             description="Detecciones de minerales hidratados dentro de la caja de contexto de Gale",
             extra={"features": len(feats)})


def main() -> None:
    cfg = c.load_config()
    if len(sys.argv) > 1:  # p. ej. `python scripts/download_layers.py boxwork` -> solo HiRISE de esas cajas
        hirise(cfg, sys.argv[1:])
        return
    hydrous(cfg)
    for name in WMTS_LAYERS:
        wmts(name, cfg)
    hirise(cfg)


if __name__ == "__main__":
    main()
