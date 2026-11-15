import numpy as np

from marswalk.geo import Grid

G = Grid(137.342, 137.392, -4.740, -4.652, 1479, 2603)  # dem_detail sidecar


def test_pixel_size_is_about_2m():
    assert abs(G.px_x_m - 1.993) < 0.01 and abs(G.px_y_m - 2.000) < 0.01


def test_roundtrip_lonlat_xy():
    lon, lat = np.array([137.35, 137.38]), np.array([-4.70, -4.66])
    lon2, lat2 = G.xy_to_lonlat(*G.lonlat_to_xy(lon, lat))
    assert np.allclose(lon, lon2) and np.allclose(lat, lat2)


def test_corners():
    assert G.lonlat_to_cell(137.342, -4.652) == (0, 0)                  # NW corner = row 0, col 0
    r, c = G.lonlat_to_cell(137.392, -4.740)                              # SE corner = last row/col
    assert (int(r), int(c)) == (2602, 1478)
    x, y = G.lonlat_to_xy(137.342, -4.740)                                # SW corner = local origin
    assert abs(x) < 1e-6 and abs(y) < 1e-6


def test_cell_center_roundtrip():
    x, y = G.rowcol_to_xy(307, 1042)
    r, c = G.xy_to_rowcol(x, y)
    assert int(np.floor(r)) == 307 and int(np.floor(c)) == 1042
