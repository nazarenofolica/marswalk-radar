"""Descarga el recorrido y los waypoints oficiales y actualizados de Curiosity (JPL, mapa MMGIS de la misión).

A diferencia de `curiosity_path` de Mars Trek (que termina en Vera Rubin Ridge), estos archivos cubren
toda la misión e incluyen el sol, lat/lon y elevación de cada parada. Sirven para ubicar el boxwork
(observado desde el sol ~4536) y para elegir fotos por sol.

Salida: data/raw/gale/vector/msl_waypoints_jpl.geojson, msl_traverse_jpl.geojson
Uso:    python scripts/download_traverse.py
"""
from __future__ import annotations

import json

import common as c

BASE = "https://mars.nasa.gov/mmgis-maps/MSL/Layers/json"


def main() -> None:
    out = c.DATA_RAW / "gale" / "vector"
    out.mkdir(parents=True, exist_ok=True)
    s = c.session()
    for src, dst in [("MSL_waypoints.json", "msl_waypoints_jpl.geojson"), ("MSL_traverse.json", "msl_traverse_jpl.geojson")]:
        url = f"{BASE}/{src}"
        data = c.get(s, url).json()
        path = out / dst
        path.write_text(json.dumps(data), encoding="utf-8")
        n = len(data.get("features", []))
        extra = {"features": n}
        sols = [f["properties"].get("sol") for f in data["features"] if isinstance(f.get("properties", {}).get("sol"), int)]
        if sols:
            extra.update(sol_min=min(sols), sol_max=max(sols))
        print(f"{dst}: {n} features {extra}, {path.stat().st_size / 1e6:.2f} MB")
        c.record(path, source=f"{url} (JPL/NASA, mapa MMGIS de la misión MSL)",
                 description=f"Curiosity {src.split('_')[1].split('.')[0]} oficial de JPL, toda la misión", extra=extra)


if __name__ == "__main__":
    main()
