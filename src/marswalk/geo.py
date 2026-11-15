"""Coordinates: lon/lat <-> local meters (x East, y North) <-> raster row/col.

Single source of truth for every coordinate conversion in the pipeline.
Model: spherical Mars (r = 3389.5 km), local equirectangular plane, origin at the SW corner of the bbox.
Rasters: row 0 is the NORTH edge, col 0 is the WEST edge (as in the data/raw sidecars).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MARS_RADIUS_M = 3_389_500.0


@dataclass(frozen=True)
class Grid:
    lon0: float  # west edge (deg E)
    lon1: float  # east edge
    lat0: float  # south edge
    lat1: float  # north edge
    width: int   # columns
    height: int  # rows

    @classmethod
    def from_sidecar(cls, meta: dict) -> "Grid":
        return cls(meta["bbox_lon"][0], meta["bbox_lon"][1], meta["bbox_lat"][0], meta["bbox_lat"][1],
                   int(meta["width_px"]), int(meta["height_px"]))

    def coarsen(self, block: int) -> "Grid":
        """Same bbox, block x block cells (edge cells replicated when dimensions are odd)."""
        return Grid(self.lon0, self.lon1, self.lat0, self.lat1,
                    math.ceil(self.width / block), math.ceil(self.height / block))

    # --- metric size -------------------------------------------------------
    @property
    def m_per_deg_lat(self) -> float:
        return math.radians(1.0) * MARS_RADIUS_M

    @property
    def m_per_deg_lon(self) -> float:
        return self.m_per_deg_lat * math.cos(math.radians((self.lat0 + self.lat1) / 2))

    @property
    def width_m(self) -> float:
        return (self.lon1 - self.lon0) * self.m_per_deg_lon

    @property
    def height_m(self) -> float:
        return (self.lat1 - self.lat0) * self.m_per_deg_lat

    @property
    def px_x_m(self) -> float:
        return self.width_m / self.width

    @property
    def px_y_m(self) -> float:
        return self.height_m / self.height

    # --- conversions (vectorized: accept scalars or numpy arrays) ----------
    def lonlat_to_xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.m_per_deg_lon, (np.asarray(lat) - self.lat0) * self.m_per_deg_lat

    def xy_to_lonlat(self, x, y):
        return self.lon0 + np.asarray(x) / self.m_per_deg_lon, self.lat0 + np.asarray(y) / self.m_per_deg_lat

    def xy_to_rowcol(self, x, y):
        """Continuous (row, col); cell (r, c) covers [r, r+1) x [c, c+1). Use np.floor for the index."""
        return self.height - np.asarray(y) / self.px_y_m, np.asarray(x) / self.px_x_m

    def rowcol_to_xy(self, row, col):
        """Center of cell (row, col) in local meters."""
        return (np.asarray(col) + 0.5) * self.px_x_m, (self.height - np.asarray(row) - 0.5) * self.px_y_m

    def lonlat_to_cell(self, lon, lat):
        """Integer (row, col) of the cell that contains (lon, lat), clipped to the grid."""
        r, c = self.xy_to_rowcol(*self.lonlat_to_xy(lon, lat))
        r = np.clip(np.floor(r).astype(int), 0, self.height - 1)
        c = np.clip(np.floor(c).astype(int), 0, self.width - 1)
        return r, c

    def contains(self, lon, lat):
        lon, lat = np.asarray(lon), np.asarray(lat)
        return (lon >= self.lon0) & (lon <= self.lon1) & (lat >= self.lat0) & (lat <= self.lat1)

    def to_dict(self) -> dict:
        return {"bbox_lon": [self.lon0, self.lon1], "bbox_lat": [self.lat0, self.lat1],
                "width_px": self.width, "height_px": self.height,
                "px_x_m": round(self.px_x_m, 6), "px_y_m": round(self.px_y_m, 6),
                "width_m": round(self.width_m, 3), "height_m": round(self.height_m, 3),
                "row0_is": "north", "col0_is": "west"}
