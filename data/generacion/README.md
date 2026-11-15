# data/generacion — heavy data generators

These files are **not stored in Git** (too heavy). After cloning the repo, regenerate them from NASA sources:

```bash
pip install -r requirements.txt
python data/generacion/generar_todo.py          # all of them (~10-15 min)
```

| Script | Output | Size | Source |
|---|---|---|---|
| `generar_fotos.py` | `data/raw/photos/` | ~26 MB | Curiosity Navcam raw images (NASA) |
| `generar_radiacion.py` | `data/raw/rad/` | ~23 MB | MSL RAD dose, NASA SPDF CDAWeb |
| `generar_clima_rems.py` | `data/raw/rems/` | ~95 MB | MSL REMS, NASA PDS Atmospheres Node |

Run them from the repo root. They use `scripts/common.py` and record every file in `data/raw/MANIFEST.json`.
`generar_fotos.py` needs the traverse files in `data/raw/gale/vector/` (already in Git).
Details of each dataset: [`data/README.md`](../README.md).
