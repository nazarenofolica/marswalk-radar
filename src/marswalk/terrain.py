"""Terrain derivatives from the DEM: slope, hillshade, hazard mask and the coarser routing grid."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from .geo import Grid


def slope_deg(dem: np.ndarray, px_x_m: float, px_y_m: float, sigma_px: float) -> np.ndarray:
    """Slope in degrees. The DEM is smoothed first (sigma in pixels) because HiRISE noise inflates 2 m slopes.

    Rows go north -> south, so the y gradient sign is irrelevant for the magnitude.
    """
    z = gaussian_filter(dem.astype(np.float64), sigma_px) if sigma_px > 0 else dem.astype(np.float64)
    gy, gx = np.gradient(z, px_y_m, px_x_m)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)


def hillshade(dem: np.ndarray, px_x_m: float, px_y_m: float, sigma_px: float,
              azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    """Classic hillshade in [0, 1], light from the NW. Row 0 = north, so dz/dnorth = -dz/drow."""
    z = gaussian_filter(dem.astype(np.float64), sigma_px) if sigma_px > 0 else dem.astype(np.float64)
    d_row, d_col = np.gradient(z, px_y_m, px_x_m)
    dz_east, dz_north = d_col, -d_row
    slope = np.arctan(np.hypot(dz_east, dz_north))
    aspect = np.arctan2(-dz_east, -dz_north)          # direction the slope faces, clockwise from north
    az, alt = np.radians(azimuth_deg), np.radians(altitude_deg)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0.0, 1.0).astype(np.float32)


def _pad_to_block(a: np.ndarray, block: int) -> np.ndarray:
    """Replicate the last row/column so both dimensions are multiples of `block`."""
    pr, pc = (-a.shape[0]) % block, (-a.shape[1]) % block
    return np.pad(a, ((0, pr), (0, pc)), mode="edge") if (pr or pc) else a


def aggregate(a: np.ndarray, block: int, how: str) -> np.ndarray:
    """Block aggregation: 'max' keeps local obstacles visible, 'mean' for elevation."""
    a = _pad_to_block(a, block)
    h, w = a.shape[0] // block, a.shape[1] // block
    v = a.reshape(h, block, w, block)
    return (v.max(axis=(1, 3)) if how == "max" else v.mean(axis=(1, 3))).astype(np.float32)


def routing_grid(dem: np.ndarray, slope: np.ndarray, grid: Grid, block: int):
    """Coarse grid used by the route engine: mean elevation and max slope per block."""
    g = grid.coarsen(block)
    elev = aggregate(dem, block, "mean")
    slp = aggregate(slope, block, "max")
    assert elev.shape == (g.height, g.width) == slp.shape
    return g, elev, slp
