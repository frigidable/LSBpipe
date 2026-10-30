"""Official Legacy Survey Viewer navigation URLs and release semantics."""
from urllib.parse import urlparse, parse_qs
import math
import pytest
from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.viewer import (
    VIEWER_ROOT, viewer_url, viewer_layer, field_polygon, field_polygon_query,
    suggested_zoom)


@pytest.mark.parametrize("release,region,expected", [
    ("dr9", None, "ls-dr9"),
    ("dr9", "north", "ls-dr9-north"),
    ("dr9", "south", "ls-dr9-south"),
    ("dr10", None, "ls-dr10"),
    ("dr10", "south", "ls-dr10-south"),
    ("dr11", None, "ls-dr11"),
    ("dr11", "north", "ls-dr9-north"),
    ("dr11", "south", "ls-dr11-south"),
])
def test_documented_release_layers(release, region, expected):
    assert viewer_layer(release, region) == expected


def test_exact_coord_zoom_and_mark_parameter():
    field = SkyField(154.7709, 46.4537, 15)
    url = viewer_url(field, release="dr9", region="north")
    assert url.startswith(VIEWER_ROOT)
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.legacysurvey.org"
    args = parse_qs(parsed.query)
    assert args == {"ra":["154.7709000"],"dec":["46.4537000"],
                    "layer":["ls-dr9-north"],"zoom":["13"],
                    "mark":["154.7709000,46.4537000"]}
    assert "www.legacysurvey.org/viewer/" in url


def test_zoom_validation_and_no_dr10_north_product_claim():
    field = SkyField(1., 0., 15.)
    for value in (-1, 21, 12.5, "13"):
        with pytest.raises(ValueError):
            viewer_url(field, zoom=value)
    with pytest.raises(ValueError):
        viewer_layer("dr10", "north")
    with pytest.raises(ValueError):
        viewer_layer("dr12")


def test_mark_is_optional_for_clean_view():
    url = viewer_url(SkyField(10, -2), mark_target=False)
    assert "mark=" not in url

@pytest.mark.parametrize("view,suffix", [
    ("image", "ls-dr9-north"),
    ("model", "ls-dr9-north-model"),
    ("resid", "ls-dr9-north-resid"),
])
def test_viewer_layer_switching(view, suffix):
    link = viewer_url(SkyField(154.7709, 46.4537), release="dr9",
                      region="north", view=view)
    assert parse_qs(urlparse(link).query)["layer"] == [suffix]


def test_viewer_rejects_unknown_preview_mode():
    with pytest.raises(ValueError, match="view"):
        viewer_url(SkyField(154.7709, 46.4537), view="dangerous")


def _tangent_offsets(center, ra_deg, dec_deg):
    ra0, dec0 = map(math.radians, (center.ra_deg, center.dec_deg))
    ra, dec = map(math.radians, (ra_deg, dec_deg))
    source = (math.cos(dec)*math.cos(ra),
              math.cos(dec)*math.sin(ra), math.sin(dec))
    radial = (math.cos(dec0)*math.cos(ra0),
              math.cos(dec0)*math.sin(ra0), math.sin(dec0))
    east = (-math.sin(ra0), math.cos(ra0), 0.)
    north = (-math.sin(dec0)*math.cos(ra0),
             -math.sin(dec0)*math.sin(ra0), math.cos(dec0))
    dot = lambda a,b: sum(x*y for x,y in zip(a,b))
    return dot(source,east)/dot(source,radial), dot(source,north)/dot(source,radial)


@pytest.mark.parametrize("ra,dec,size", [
    (40.096208, 1.229250, 5),
    (154.7709, 46.4537, 5),
    (359.999, -20., 20),
    (0.001, 89.99, 5),
    (145.55, -80.3, 120),
])
def test_tangent_plane_square_has_correct_angular_side_and_closed_outline(ra,dec,size):
    field = SkyField(ra, dec, size)
    polygon = field_polygon(field, subdivisions=4)
    assert len(polygon) == 17
    assert polygon[-1] == polygon[0]
    assert all(0 <= a < 360 and -90 <= b <= 90 for a,b in polygon)
    half = math.tan(math.radians(size / 120))
    projected = [_tangent_offsets(field, a, b) for a,b in polygon[:-1]]
    for edge in range(4):
        points = projected[edge*4:edge*4+4]
        assert len(points) == 4
        if edge in (0,2):  # bottom/top constant north coordinate
            assert all(p[1] == pytest.approx((-1 if edge==0 else 1)*half, abs=2e-12)
                       for p in points)
        else:  # left/right constant east coordinate
            assert all(p[0] == pytest.approx((1 if edge==1 else -1)*half, abs=2e-12)
                       for p in points)
    assert min(x for x,_ in projected) == pytest.approx(-half,abs=2e-12)
    assert max(x for x,_ in projected) == pytest.approx(half,abs=2e-12)
    assert min(y for _,y in projected) == pytest.approx(-half,abs=2e-12)
    assert max(y for _,y in projected) == pytest.approx(half,abs=2e-12)


def test_viewer_official_poly_parameter_tracks_field_without_changing_legacy_api():
    field = SkyField(40.096208, 1.22925, 5)
    parsed = parse_qs(urlparse(viewer_url(field, show_field=True)).query)
    assert parsed['poly'] == [field_polygon_query(field)]
    assert parsed['mark'] == ["40.0962080,1.2292500"]
    assert parsed['zoom'] == ['13']
    points = list(map(float, parsed['poly'][0].split(',')))
    assert len(points) == 34
    assert points[:2] == points[-2:]
    assert "poly" not in parse_qs(urlparse(viewer_url(field)).query)


def test_preview_recenter_does_not_move_selected_search_outline():
    center = SkyField(40.0, 1.25, 5)
    brick = SkyField(40.3, 1.5, 5)
    url = viewer_url(brick, show_field=True, overlay_field=center, mark_target=False)
    params = parse_qs(urlparse(url).query)
    assert params['ra'] == ['40.3000000']
    assert params['poly'] == [field_polygon_query(center)]
    assert 'mark' not in params


@pytest.mark.parametrize('subdivisions',[0,-1,33,0.5,'4'])
def test_invalid_polygon_sampling_is_rejected(subdivisions):
    with pytest.raises(ValueError, match='subdivisions'):
        field_polygon(SkyField(40,1,5),subdivisions=subdivisions)


@pytest.mark.parametrize("width,zoom", [
    (0.1, 19), (1, 15), (5, 13), (15, 11), (30, 10), (120, 8)])
def test_suggested_zoom_frames_field_without_changing_angular_outline(width, zoom):
    field = SkyField(154.7709,46.4537,width)
    assert suggested_zoom(field) == zoom
    assert 'poly' in parse_qs(urlparse(viewer_url(
        field, show_field=True, zoom=suggested_zoom(field))).query)
