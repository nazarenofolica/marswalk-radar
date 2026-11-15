"""Utilidades compartidas por los scripts de descarga.

Uso: ejecutar desde la raíz del repo con `python scripts/<script>.py` (nunca desde dentro de data/).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
CONFIG_PATH = ROOT / "configs" / "sites" / "gale.yaml"
MANIFEST_PATH = ROOT / "data" / "raw" / "MANIFEST.json"

# Marte: radio medio 3389,5 km -> 1 grado = 59,16 km
KM_PER_DEG = 59.16
USER_AGENT = "marswalk-hackathon/0.1 (NASA Space Apps 2026; data download script)"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get(s: requests.Session, url: str, *, retries: int = 4, timeout: int = 120, **kw) -> requests.Response:
    """GET con reintentos y espera creciente. Falla con excepción si no se logra."""
    last = None
    for i in range(retries):
        try:
            r = s.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last = e
            wait = 2 ** (i + 1)
            print(f"  reintento {i + 1}/{retries} en {wait}s ({type(e).__name__})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


def px_size_deg(pixel_m: float) -> float:
    return pixel_m / (KM_PER_DEG * 1000.0)


def bbox_size_px(lon: list[float], lat: list[float], pixel_m: float) -> tuple[int, int]:
    d = px_size_deg(pixel_m)
    return round((lon[1] - lon[0]) / d), round((lat[1] - lat[0]) / d)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _locked_update(fn) -> None:
    """Lee el manifiesto, aplica fn(manifest) y lo guarda, con un .lock para scripts en paralelo."""
    lock = MANIFEST_PATH.with_suffix(".lock")
    for _ in range(600):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        raise RuntimeError(f"No se pudo tomar {lock}; si no hay otro script corriendo, borrarlo a mano")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {}
        fn(manifest)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def forget(prefix: str) -> None:
    """Quita del manifiesto las entradas cuya ruta empieza con `prefix` (p. ej. antes de re-descargar)."""
    _locked_update(lambda m: [m.pop(k) for k in [k for k in m if k.startswith(prefix)]])


def record(path: Path, *, source: str, description: str, extra: dict | None = None) -> None:
    """Agrega o actualiza la entrada del archivo en data/raw/MANIFEST.json.

    Usa un archivo .lock para que dos scripts corriendo en paralelo no se pisen.
    """
    entry = {
        "source": source,
        "description": description,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **(extra or {}),
    }
    _locked_update(lambda m: m.__setitem__(path.relative_to(ROOT).as_posix(), entry))
