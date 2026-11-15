"""Corre todas las descargas en el orden correcto.

Uso (desde la raíz del repo):  python scripts/download_all.py
Tarda ~15-25 minutos según la conexión. Cada script se puede correr también por separado.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ["download_vectors.py"],                  # ruta y waypoints de Curiosity (Mars Trek)
    ["download_traverse.py"],                 # ruta y waypoints oficiales de JPL (toda la misión)
    ["download_dem.py"],                      # DEM de Gale: detail, route_full, boxwork, context
    ["download_layers.py"],                   # HiRISE, CTX, THEMIS, geología, minerales hidratados
    ["../data/generacion/generar_todo.py"],   # datos pesados (no van a Git): radiación, fotos, clima REMS
]


def main() -> None:
    for step in STEPS:
        print(f"\n=== {' '.join(step)} ===", flush=True)
        r = subprocess.run([sys.executable, str(HERE / step[0]), *step[1:]], cwd=HERE.parent)
        if r.returncode != 0:
            sys.exit(f"Falló {step[0]} (código {r.returncode}). Se puede reintentar ese script solo.")
    print("\nListo. Detalle de cada archivo en data/raw/MANIFEST.json")


if __name__ == "__main__":
    main()
