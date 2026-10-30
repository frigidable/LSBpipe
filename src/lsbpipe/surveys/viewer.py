"""Stable, URL-based navigation to the official Legacy Survey Sky Viewer.

The hosted Imagine/Leaflet application is a *preview* and is independent of
LSBpipe's authoritative per-brick product discovery. Its URL is opened as a
top-level QtWebEngine page (not iframe) or in the user's browser.
"""
from __future__ import annotations
from urllib.parse import urlencode
from .legacy import SkyField, regions_for_release

VIEWER_ROOT = "https://www.legacysurvey.org/viewer/"

# North imaging is DR9 MzLS/BASS even in the combined DR10/DR11 layers.
_LAYER_MAP = {
    ("dr9", None): "ls-dr9",
    ("dr9", "north"): "ls-dr9-north",
    ("dr9", "south"): "ls-dr9-south",
    ("dr10", None): "ls-dr10",
    ("dr10", "south"): "ls-dr10-south",
    ("dr11", None): "ls-dr11",
    ("dr11", "north"): "ls-dr9-north",
    ("dr11", "south"): "ls-dr11-south",
}


def viewer_layer(release: str, region: str | None = None) -> str:
    """Choose an explicitly documented imaging layer.

    DR10's combined layer includes northern DR9, but DR10 has no standalone
    north data set. Explicit (dr10,north) is therefore forbidden.
    """
    regions_for_release(release)
    key = (release, region)
    if key not in _LAYER_MAP:
        raise ValueError(f"Unsupported Legacy viewer release/region: {key!r}")
    return _LAYER_MAP[key]


def viewer_url(field: SkyField, *, release: str = "dr9",
               region: str | None = None, zoom: int = 13,
               mark_target: bool = True) -> str:
    """Create an exact-coordinate launch URL to the *hosted* Legacy viewer.

    Zoom is a Leaflet integer zoom level and is not directly a field width in
    arcminutes; the GUI retains field width independently for brick discovery.
    """
    if not isinstance(zoom, int) or not 0 <= zoom <= 20:
        raise ValueError("zoom must be an integer from 0 to 20")
    layer = viewer_layer(release, region)
    args = {"ra":format(field.ra_deg, ".7f"),
            "dec":format(field.dec_deg, ".7f"),
            "zoom":str(zoom), "layer":layer}
    if mark_target:
        args["mark"] = f"{field.ra_deg:.7f},{field.dec_deg:.7f}"
    return VIEWER_ROOT + "?" + urlencode(args)
