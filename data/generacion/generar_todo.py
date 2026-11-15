"""Genera todos los datos pesados que NO se versionan en Git: fotos, radiación y clima.

Uso (desde la raíz del repo):  python data/generacion/generar_todo.py
Tarda ~10-15 minutos. Cada script también se puede correr solo:
  python data/generacion/generar_fotos.py        -> data/raw/photos/   (~26 MB, 56 fotos Navcam)
  python data/generacion/generar_radiacion.py    -> data/raw/rad/      (~23 MB, dosis RAD 2012-2026)
  python data/generacion/generar_clima_rems.py   -> data/raw/rems/     (~95 MB, 8 soles de REMS)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STEPS = ["generar_radiacion.py", "generar_fotos.py", "generar_clima_rems.py"]


def main() -> None:
    for step in STEPS:
        print(f"\n=== {step} ===", flush=True)
        r = subprocess.run([sys.executable, str(HERE / step)], cwd=ROOT)
        if r.returncode != 0:
            sys.exit(f"Falló {step} (código {r.returncode}). Se puede reintentar ese script solo.")
    print("\nListo. Detalle de cada archivo en data/raw/MANIFEST.json")


if __name__ == "__main__":
    main()
