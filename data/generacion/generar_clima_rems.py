"""Descarga datos de clima de Curiosity (REMS, PDS Atmospheres Node) para unos pocos soles representativos.

Por cada sol baja el archivo RMD (datos ambientales corregidos, tabla de texto a 1 Hz) y su etiqueta
PDS4 (.xml), que define las columnas. Cada sol pesa ~11 MB: por eso solo se bajan los soles de
`rems.sols` en configs/sites/gale.yaml.
Notas del formato: tabla sin encabezado; las columnas están en la etiqueta .xml; -999 = sin dato.

Salida: data/raw/rems/SOL<n>/...
Uso:    python data/generacion/generar_clima_rems.py   (desde la raíz del repo)
"""
from __future__ import annotations

import re

import sys
from pathlib import Path

# common.py vive en scripts/ (raíz del repo)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import common as c  # noqa: E402


def listing(s, url: str) -> list[str]:
    html = c.get(s, url).text
    return re.findall(r'href="([^"?/][^"]*)"', html)


def main() -> None:
    cfg = c.load_config()["rems"]
    base = cfg["base_url"].rstrip("/")
    s = c.session()
    ranges = [h.rstrip("/") for h in listing(s, base + "/") if h.startswith("SOL_")]
    out_root = c.DATA_RAW / "rems"
    for sol in cfg["sols"]:
        rng = next((r for r in ranges if int(r.split("_")[1]) <= sol <= int(r.split("_")[2])), None)
        if not rng:
            print(f"sol {sol}: fuera de rango")
            continue
        folder = f"{base}/{rng}/SOL{sol:05d}"
        files = listing(s, folder + "/")
        wanted = [f for f in files if "RMD" in f and (f.endswith(".TAB") or f.endswith(".xml"))]
        if not wanted:
            print(f"sol {sol}: no hay archivos RMD ({len(files)} archivos en la carpeta)")
            continue
        out = out_root / f"SOL{sol:05d}"
        out.mkdir(parents=True, exist_ok=True)
        for f in wanted:
            path = out / f
            path.write_bytes(c.get(s, f"{folder}/{f}", timeout=600).content)
            c.record(path, source=f"{folder}/{f} (PDS Atmospheres Node, NASA)",
                     description=f"REMS MODRDR (RMD) de Curiosity, sol {sol}")
        print(f"sol {sol}: {', '.join(wanted)}")


if __name__ == "__main__":
    main()
