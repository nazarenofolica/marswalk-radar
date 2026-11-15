"""Descarga fotos de punto de vista de Curiosity (imágenes crudas públicas) para cada parada.

Paradas = los 14 waypoints con nombre de Mars Trek + 3 paradas en la zona de boxwork. Para cada una se busca
el sol en que Curiosity estuvo más cerca (waypoints oficiales de JPL) y se bajan hasta 4 imágenes completas
que miren al horizonte (elevación del mástil entre -25° y +10°), repartidas en distintas direcciones.
Prioriza Navcam (gran angular); Mastcam como respaldo. Si ese sol no tiene, prueba soles vecinos (hasta ±10).

Usa data/raw/gale/vector/curiosity_waypoints.geojson y msl_waypoints_jpl.geojson (ya están en el repo).
Crédito obligatorio de las imágenes: NASA/JPL-Caltech/MSSS (Mastcam) o NASA/JPL-Caltech (Navcam).

Salida: data/raw/photos/<parada>/<imageid>.jpg y data/raw/photos/index.json
Uso:    python data/generacion/generar_fotos.py   (desde la raíz del repo)
"""
from __future__ import annotations

import json
import math
import re
import shutil

import sys
from pathlib import Path

# common.py vive en scripts/ (raíz del repo)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import common as c  # noqa: E402

API = "https://mars.nasa.gov/api/v1/raw_image_items/"
PER_STOP = 4
# Navcam primero: gran angular, ideal como vista de paisaje. Mastcam como respaldo.
PREFERRED = ["NAV_LEFT_B", "NAV_RIGHT_B", "MAST_LEFT", "MAST_RIGHT"]
# Elevación del mástil (grados) para una vista "hacia el horizonte". Las tomas del brazo/suelo están en ~ -47.
EL_MIN, EL_MAX = -25.0, 10.0
# Paradas en la zona de boxwork (soles con waypoints oficiales, ver documentos/PLAN.md)
BOXWORK_SOLS = {"Boxwork - first close-up (sol 4536)": 4536, "Boxwork - mid (sol 4602)": 4602,
                "Boxwork - south (sol 4669)": 4669}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def dist_km(a, b) -> float:
    dl = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return 3389.5 * math.hypot(dl, math.radians(b[1] - a[1]))


def images_for_sol(s, sol: int) -> list[dict]:
    items, page = [], 0
    while True:
        r = c.get(s, API, params={"order": "sol desc", "per_page": 100, "page": page, "condition_1": "msl:mission",
                                  "condition_2": f"{sol}:sol:gte", "condition_3": f"{sol}:sol:lte"}).json()
        items += r.get("items", [])
        if len(items) >= r.get("total", 0) or not r.get("items") or page > 10:
            return items
        page += 1


def _el(i: dict):
    try:
        return float((i.get("extended") or {}).get("mast_el"))
    except (TypeError, ValueError):
        return None


def pick(items: list[dict]) -> list[dict]:
    """Imágenes completas, apuntando al horizonte, repartidas en distintas direcciones (azimut)."""
    full = [i for i in items if (i.get("extended") or {}).get("sample_type") == "full" and not i.get("is_thumbnail")]
    for inst in PREFERRED:
        sel = [i for i in full if i["instrument"] == inst and _el(i) is not None and EL_MIN <= _el(i) <= EL_MAX]
        if sel:
            sel.sort(key=lambda i: float((i.get("extended") or {}).get("mast_az") or 0))
            step = max(1, len(sel) // PER_STOP)
            return sel[::step][:PER_STOP]
    return []


def main() -> None:
    vec = c.DATA_RAW / "gale" / "vector"
    trek = json.loads((vec / "curiosity_waypoints.geojson").read_text(encoding="utf-8"))
    jpl = [f["properties"] for f in json.loads((vec / "msl_waypoints_jpl.geojson").read_text(encoding="utf-8"))["features"]]
    stops = {}
    for f in trek["features"]:
        lonlat = f["geometry"]["coordinates"]
        near = min(jpl, key=lambda p: dist_km(lonlat, (p["lon"], p["lat"])))
        stops[f["properties"]["name"]] = (near["sol"], lonlat, round(dist_km(lonlat, (near["lon"], near["lat"])) * 1000))
    for name, sol in BOXWORK_SOLS.items():
        p = min(jpl, key=lambda q: abs(q["sol"] - sol))
        stops[name] = (sol, [p["lon"], p["lat"]], 0)

    s = c.session()
    out_root = c.DATA_RAW / "photos"
    # Empezar limpio: borrar fotos anteriores y sus entradas del manifiesto
    if out_root.exists():
        shutil.rmtree(out_root)
    c.forget("data/raw/photos/")
    out_root.mkdir(parents=True)
    index = []
    for name, (sol, lonlat, err_m) in stops.items():
        chosen, used_sol = [], sol
        for delta in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 7, -7, 10, -10]:
            chosen = pick(images_for_sol(s, sol + delta))
            if chosen:
                used_sol = sol + delta
                break
        if not chosen:
            print(f"{name}: sin imágenes completas cerca del sol {sol}")
            continue
        folder = out_root / slug(name)
        folder.mkdir(parents=True, exist_ok=True)
        for it in chosen:
            url = it.get("https_url") or it["url"]
            path = folder / (it["imageid"] + ".jpg")
            path.write_bytes(c.get(s, url).content)
            credit = it.get("image_credit") or "NASA/JPL-Caltech"
            index.append({"stop": name, "stop_lon": lonlat[0], "stop_lat": lonlat[1], "stop_match_error_m": err_m,
                          "sol": it["sol"], "instrument": it["instrument"], "date_taken": it.get("date_taken"),
                          "file": path.relative_to(c.ROOT).as_posix(), "url": url, "credit": credit})
            c.record(path, source=f"{url} (NASA raw images API)", description=f"Curiosity {it['instrument']} sol {it['sol']} - {name}",
                     extra={"credit": credit})
        print(f"{name}: sol {used_sol} (pedido {sol}, waypoint a {err_m} m), {len(chosen)} imagen(es) {chosen[0]['instrument']}")
    (out_root / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Total: {len(index)} imágenes")


if __name__ == "__main__":
    main()
