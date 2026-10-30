"""Official Legacy Survey Viewer navigation URLs and release semantics."""
from urllib.parse import urlparse, parse_qs
import pytest
from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.viewer import VIEWER_ROOT, viewer_url, viewer_layer


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
