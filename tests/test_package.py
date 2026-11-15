from pathlib import Path

import pytest

from marswalk.package import validate

PKG = Path(__file__).resolve().parents[1] / "data" / "processed" / "gale_radar"


@pytest.mark.skipif(not (PKG / "site.json").exists(), reason="package not built yet")
def test_built_package_is_valid():
    assert validate(PKG) == []
