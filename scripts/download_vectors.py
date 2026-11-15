"""Descarga la ruta y los waypoints de Curiosity (Mars Trek) y los guarda como GeoJSON.

Salida: data/raw/gale/vector/curiosity_path.geojson, curiosity_waypoints.geojson
"""
from __future__ import annotations

import json

import common as c


def esri_to_geojson(esri: dict, kind: str) -> dict:
    feats = []
    for f in esri["features"]:
        g = f["geometry"]
        if kind == "point":
            geom = {"type": "Point", "coordinates": [g["x"], g["y"]]}
        else:
            geom = {"type": "LineString", "coordinates": g["paths"][0]}
            if len(g["paths"]) > 1:
                geom = {"type": "MultiLineString", "coordinates": g["paths"]}
        feats.append({"type": "Feature", "properties": f["attributes"], "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def main() -> None:
    cfg = c.load_config()
    base = cfg["trek"]["arcgis"]
    out = c.DATA_RAW / "gale" / "vector"
    out.mkdir(parents=True, exist_ok=True)
    s = c.session()
    for key, kind, fname in [
        ("path", "line", "curiosity_path.geojson"),
        ("waypoints", "point", "curiosity_waypoints.geojson"),
    ]:
        svc = cfg["trek"]["services"][key]
        url = f"{base}/{svc}/MapServer/0/query"
        r = c.get(
            s,
            url,
            params={"where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": 4326, "f": "json"},
        )
        data = esri_to_geojson(r.json(), kind)
        path = out / fname
        path.write_text(json.dumps(data), encoding="utf-8")
        n = len(data["features"])
        print(f"{fname}: {n} feature(s), {path.stat().st_size / 1e3:.0f} KB")
        c.record(
            path,
            source=f"{url} (Mars Trek, JPL/NASA)",
            description=f"Curiosity {key} convertido a GeoJSON (EPSG:4326)",
            extra={"features": n},
        )


if __name__ == "__main__":
    main()
