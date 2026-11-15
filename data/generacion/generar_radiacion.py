"""Descarga la serie de dosis de radiación de Curiosity (RAD) desde CDAWeb (NASA SPDF), un CSV por año.

Dataset MSL_RAD_OBS-L2, variable DOSIMETRY_TOTAL_f (calidad filtrada). Columnas del CSV:
  EPOCH (UTC), TOTAL_DOSE_B (µGy/día, silicio), RTG_CORRECTED_DOSE_B (µGy/día, sin el aporte del
  generador del rover), TOTAL_DOSE_E (µGy/día, plástico equivalente a tejido).
Es DOSIS ABSORBIDA, no dosis equivalente (mSv). Ver documentos/PLAN.md, sección 5.

Salida: data/raw/rad/msl_rad_dose_<año>.csv
Uso:    python data/generacion/generar_radiacion.py   (desde la raíz del repo)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import sys
from pathlib import Path

# common.py vive en scripts/ (raíz del repo)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import common as c  # noqa: E402

WS = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets"


def main() -> None:
    cfg = c.load_config()["rad"]
    start = datetime.strptime(cfg["start"], "%Y-%m-%d").date()
    end = datetime.strptime(cfg["end"], "%Y-%m-%d").date()
    out = c.DATA_RAW / "rad"
    out.mkdir(parents=True, exist_ok=True)
    s = c.session()
    url_base = f"{WS}/{cfg['dataset']}/data"
    for year in range(start.year, end.year + 1):
        a_year = max(start, date(year, 1, 1))
        b_year = min(end, date(year, 12, 31)) + timedelta(days=1)
        header: list[str] = []
        data_rows: list[str] = []

        def fetch(a: date, b: date) -> None:
            """CDAWeb rechaza (503) pedidos con demasiados datos: si falla, parte el rango a la mitad."""
            rng = f"{a:%Y%m%d}T000000Z,{b:%Y%m%d}T000000Z"
            try:
                meta = c.get(s, f"{url_base}/{rng}/{cfg['variable']}", params={"format": "csv"},
                             headers={"Accept": "application/json"}, retries=2).json()
            except RuntimeError:
                if (b - a).days <= 2:
                    raise
                mid = a + (b - a) / 2
                fetch(a, mid)
                fetch(mid, b)
                return
            files = meta.get("FileDescription") or []
            if not files:
                return
            lines = c.get(s, files[0]["Name"], timeout=300).content.decode("utf-8", "replace").splitlines()
            if not header:
                header.extend(ln for ln in lines if not ln[:2].isdigit())
            data_rows.extend(ln for ln in lines if ln[:2].isdigit())

        # Pedidos mensuales (un año entero da 503)
        a = a_year
        while a < b_year:
            nxt = date(a.year + (a.month == 12), a.month % 12 + 1, 1)
            fetch(a, min(nxt, b_year))
            a = nxt
        if not data_rows:
            print(f"{year}: sin datos")
            continue
        path = out / f"msl_rad_dose_{year}.csv"
        path.write_text("\n".join(header + data_rows) + "\n", encoding="utf-8")
        print(f"{year}: {len(data_rows)} mediciones, {path.stat().st_size / 1e6:.2f} MB")
        c.record(path, source=f"{url_base}/<rango trimestral>/{cfg['variable']}?format=csv (CDAWeb, NASA SPDF)",
                 description=f"Dosis RAD de Curiosity en superficie, {a_year} a {b_year - timedelta(days=1)} (µGy/día, dosis absorbida)",
                 extra={"rows": len(data_rows)})


if __name__ == "__main__":
    main()
