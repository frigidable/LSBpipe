"""Offline geometry / interaction contract for Legacy brick overlays."""
import pytest
from urllib.parse import parse_qs, urlparse

from lsbpipe.surveys.legacy import BrickHit, SkyField
from lsbpipe.surveys.brick_overlay import (
    brick_shapes, installation_script, highlight_script)
from lsbpipe.surveys.viewer import viewer_url


def _hit(release, name, ra1=40.0, ra2=40.25):
    return BrickHit(release, "north", name, ra1, ra2, 1., 1.25,
                    {"g":3, "r":3, "z":3})


def test_shared_dr9_dr11_same_footprint_not_duplicate_overlay():
    hits = [_hit("dr9", "0401p012"), _hit("dr11", "0401p012")]
    features, rows = brick_shapes(hits)
    assert len(features) == 1
    assert rows == ["0", "0"]
    assert features[0]["releases"] == ["dr9", "dr11"]


def test_ra_wrap_brick_is_small_on_sky_not_a_360_degree_arc():
    hit = _hit("dr9", "3599p012", 359.9, .1)
    feats, rows = brick_shapes([hit])
    assert rows == ["0"]
    ra_values = [pt[0] for pt in feats[0]["polygon"]]
    assert max(ra_values)-min(ra_values) == pytest.approx(.2)


def test_invalid_geometry_skipped():
    features, rows = brick_shapes([
        _hit("dr9", "bad-name"), _hit("dr9", "0401p012", 40., 40.)])
    assert features == [] and rows == [None, None]


def test_scripts_use_escaped_json_and_origin_guard():
    feature, _ = brick_shapes([_hit("dr9", "0401p012")])
    script = installation_script(feature)
    assert "location.origin" in script
    assert "www.legacysurvey.org" in script
    assert "fillOpacity" in script
    assert "mouseover" in script and "mouseout" in script
    assert "click" in script
    assert "__FEATURES__" not in script
    assert "0401p012" in script
    assert 'hover("0")' in highlight_script("0", hover=True)
    assert 'select("0")' in highlight_script("0")


def test_official_url_contains_all_distinct_bounds_fallback():
    bricks = [_hit("dr9","0401p012"), _hit("dr11","0401p012"),
              _hit("dr9","0402p012",40.25,40.5)]
    query = parse_qs(urlparse(viewer_url(
        SkyField(40.12,1.15,5),show_field=True,bricks=bricks)).query)
    assert len(query["poly"][0].split(";")) == 3
