"""Route engine on the routing grid.

Each walkable cell is a node; edges go to the 8 neighbours. Edge cost = walking time (minutes), using a
Tobler-type hiking curve (faster slightly downhill, slower uphill and on steep descents), scaled so that
flat ground is walked at `base_speed_kmh`. This curve is an EXPLORATORY ASSUMPTION for a suited astronaut,
not a validated model.

Rules (see documentos/PLAN.md):
- one hard constraint: a cell whose (max-aggregated) slope exceeds the threshold is not walkable;
- no corner cutting: a diagonal move needs both orthogonal neighbours walkable;
- directed graph: uphill and downhill cost differ;
- if two points are not connected, the engine says so (returns None) and never relaxes the constraint.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

TOBLER_FLAT = 6.0 * math.exp(-3.5 * 0.05)  # km/h of the original curve on flat ground (~5.04)


def speed_kmh(grade: np.ndarray, base_speed_kmh: float) -> np.ndarray:
    """Walking speed for a signed grade (dz/dx). Tobler (1993) scaled to base speed on flat ground."""
    return base_speed_kmh / TOBLER_FLAT * 6.0 * np.exp(-3.5 * np.abs(grade + 0.05))


class RouteGraph:
    def __init__(self, elev: np.ndarray, slope: np.ndarray, px_x_m: float, px_y_m: float,
                 max_slope_deg: float, base_speed_kmh: float):
        self.h, self.w = elev.shape
        self.elev, self.slope = elev, slope
        self.px_x, self.px_y = px_x_m, px_y_m
        self.max_slope = max_slope_deg
        self.base_speed = base_speed_kmh
        self.walkable = slope <= max_slope_deg
        self.G = self._build()
        self._GT = None

    def node(self, r: int, c: int) -> int:
        return int(r) * self.w + int(c)

    def rc(self, n):
        return np.divmod(n, self.w)

    def _build(self) -> csr_matrix:
        h, w = self.h, self.w
        ok = self.walkable
        rows, cols, data = [], [], []
        idx = np.arange(h * w).reshape(h, w)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                # source block and the same block shifted by (dr, dc) = destination
                rs = slice(max(0, -dr), h - max(0, dr)); cs = slice(max(0, -dc), w - max(0, dc))
                rd = slice(rs.start + dr, rs.stop + dr); cd = slice(cs.start + dc, cs.stop + dc)
                m = ok[rs, cs] & ok[rd, cd]
                if dr != 0 and dc != 0:  # no corner cutting
                    m &= ok[rd, cs] & ok[rs, cd]
                dist = math.hypot(dr * self.px_y, dc * self.px_x)
                dz = self.elev[rd, cd] - self.elev[rs, cs]
                t_min = dist / 1000.0 / speed_kmh(dz / dist, self.base_speed) * 60.0
                rows.append(idx[rs, cs][m]); cols.append(idx[rd, cd][m]); data.append(t_min[m])
        return csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(h * w, h * w))

    def nearest_walkable(self, r: int, c: int, max_radius: int = 50):
        """Snap a point to the closest walkable cell (stops can fall on a steep pixel)."""
        if self.walkable[r, c]:
            return r, c, 0.0
        for rad in range(1, max_radius + 1):
            r0, r1 = max(0, r - rad), min(self.h, r + rad + 1)
            c0, c1 = max(0, c - rad), min(self.w, c + rad + 1)
            rr, cc = np.nonzero(self.walkable[r0:r1, c0:c1])
            if len(rr):
                d = np.hypot((rr + r0 - r) * self.px_y, (cc + c0 - c) * self.px_x)
                k = int(np.argmin(d))
                return int(rr[k] + r0), int(cc[k] + c0), float(d[k])
        raise ValueError(f"no walkable cell within {max_radius} cells of ({r}, {c})")

    def shortest_path(self, src: tuple[int, int], dst: tuple[int, int]):
        """List of (row, col) from src to dst, or None if they are not connected under the constraint."""
        s, t = self.node(*src), self.node(*dst)
        dist, pred = dijkstra(self.G, directed=True, indices=s, return_predecessors=True)
        if not np.isfinite(dist[t]):
            return None
        path = [t]
        while path[-1] != s:
            path.append(pred[path[-1]])
        path.reverse()
        r, c = self.rc(np.array(path))
        return list(zip(r.tolist(), c.tolist()))

    def time_to(self, dst: tuple[int, int]) -> np.ndarray:
        """Minutes to walk from every cell TO dst (reverse graph). inf = not reachable."""
        if self._GT is None:
            self._GT = self.G.T.tocsr()
        return dijkstra(self._GT, directed=True, indices=self.node(*dst)).reshape(self.h, self.w)

    def path_metrics(self, path):
        """Per-segment distance (m), time (min), signed grade, plus cumulative values."""
        r = np.array([p[0] for p in path]); c = np.array([p[1] for p in path])
        dx = np.diff(c) * self.px_x; dy = np.diff(r) * self.px_y
        seg = np.hypot(dx, dy)
        dz = np.diff(self.elev[r, c])
        t = seg / 1000.0 / speed_kmh(dz / np.where(seg > 0, seg, 1), self.base_speed) * 60.0
        return seg, t, dz

    def verify(self, path) -> dict:
        """Post-check: no cell of the path is blocked and no diagonal cuts a blocked corner."""
        bad = [p for p in path if not self.walkable[p]]
        corners = 0
        for (r0, c0), (r1, c1) in zip(path[:-1], path[1:]):
            if r0 != r1 and c0 != c1 and not (self.walkable[r1, c0] and self.walkable[r0, c1]):
                corners += 1
        return {"blocked_cells_on_path": len(bad), "corner_cuts": corners, "ok": not bad and corners == 0}
