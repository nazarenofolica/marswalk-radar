import numpy as np

from marswalk.route import RouteGraph, speed_kmh


def flat(h=20, w=20):
    return np.zeros((h, w), np.float32), np.zeros((h, w), np.float32)


def test_speed_is_base_on_flat_and_slower_uphill():
    assert abs(float(speed_kmh(np.array(0.0), 2.5)) - 2.5) < 1e-9
    assert speed_kmh(np.array(0.2), 2.5) < speed_kmh(np.array(-0.05), 2.5)


def test_goes_around_a_wall():
    elev, slope = flat()
    slope[2:18, 10] = 45  # wall with gaps at the top and bottom
    g = RouteGraph(elev, slope, 4.0, 4.0, 20, 2.5)
    p = g.shortest_path((10, 2), (10, 17))
    assert p is not None and all(slope[r, c] <= 20 for r, c in p)
    assert g.verify(p)["ok"]


def test_no_corner_cutting():
    elev, slope = flat(5, 5)
    slope[1, 2] = slope[2, 1] = 45          # diagonal (1,1)->(2,2) would cut both corners
    g = RouteGraph(elev, slope, 4.0, 4.0, 20, 2.5)
    p = g.shortest_path((1, 1), (2, 2))
    assert p is not None and len(p) > 2 and g.verify(p)["corner_cuts"] == 0


def test_reports_no_path():
    elev, slope = flat()
    slope[:, 10] = 45  # full wall
    g = RouteGraph(elev, slope, 4.0, 4.0, 20, 2.5)
    assert g.shortest_path((10, 2), (10, 17)) is None
    assert not np.isfinite(g.time_to((10, 17))[10, 2])
