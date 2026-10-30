"""Stable, URL-based navigation to the official Legacy Survey Sky Viewer.

The hosted Imagine/Leaflet application is a *preview* and is independent of
LSBpipe's authoritative per-brick product discovery. Its URL is opened as a
top-level QtWebEngine page (not iframe) or in the user's browser.
"""
from __future__ import annotations
from urllib.parse import urlencode
import math
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



def field_polygon(field: SkyField, *, subdivisions: int = 4
                  ) -> tuple[tuple[float, float], ...]:
    """Closed square search-area boundary on the local ICRS TAN plane.

    The width is the side length of the region in arcminutes, not the
    screen size. Four equal-length sides in the local tangent plane are
    sampled as sky positions and returned as (RA deg, Dec deg) vertices.
    The great-circle connections in the hosted Viewer approximate these
    curved TAN edges; subdivisions improve that approximation. This is
    display geometry only, not a new discovery/coverage acceptance rule.
    """
    if not isinstance(subdivisions, int) or not 1 <= subdivisions <= 32:
        raise ValueError("subdivisions must be an integer between 1 and 32")
    ra = math.radians(field.ra_deg)
    dec = math.radians(field.dec_deg)
    center = (math.cos(dec)*math.cos(ra),
              math.cos(dec)*math.sin(ra), math.sin(dec))
    east = (-math.sin(ra), math.cos(ra), 0.)
    north = (-math.sin(dec)*math.cos(ra),
             -math.sin(dec)*math.sin(ra), math.cos(dec))
    half = math.tan(math.radians(field.width_arcmin / 120.))

    # Counter-clockwise boundary; every successive coordinate corresponds
    # to a sample on a projected straight edge, not an RA/Dec rectangle.
    corners = ((-half, -half), (half, -half),
               (half, half), (-half, half))
    coords = []
    for i, (u0, v0) in enumerate(corners):
        u1, v1 = corners[(i + 1) % 4]
        for j in range(subdivisions):
            t = j / subdivisions
            u = u0 + (u1-u0)*t
            v = v0 + (v1-v0)*t
            x = center[0] + u*east[0] + v*north[0]
            y = center[1] + u*east[1] + v*north[1]
            z = center[2] + u*east[2] + v*north[2]
            sky_ra = math.degrees(math.atan2(y, x)) % 360
            sky_dec = math.degrees(math.atan2(z, math.hypot(x,y)))
            coords.append((sky_ra, sky_dec))
    coords.append(coords[0])  # make the closed outline explicit in poly
    return tuple(coords)


def field_polygon_query(field: SkyField) -> str:
    """Value of official Legacy Viewer poly= URL parameter (RA,Dec pairs)."""
    points = field_polygon(field)
    return ",".join(f"{value:.9f}" for pair in points for value in pair)



def suggested_zoom(field: SkyField) -> int:
    """Approximate map framing for the requested width.

    Legacy Viewer zoom depends on the client viewport; this choice is a
    navigation convenience, NOT a guarantee that the polygon fills a
    fixed fraction of the on-screen map. The official poly annotation
    remains authoritative for the selected angular footprint.
    """
    return max(0, min(20, round(
        13 + math.log2(5.0 / field.width_arcmin))))



def viewer_url(field: SkyField, *, release: str = "dr9",
               region: str | None = None, zoom: int = 13,
               mark_target: bool = True,
               view: str = "image",
               show_field: bool = False,
               overlay_field: SkyField | None = None) -> str:
    """Create an exact-coordinate launch URL to the *hosted* Legacy viewer.

    Zoom is a Leaflet integer zoom level and is not directly a field width in
    arcminutes; the GUI retains field width independently for brick discovery.
    """
    if not isinstance(zoom, int) or not 0 <= zoom <= 20:
        raise ValueError("zoom must be an integer from 0 to 20")
    layer = viewer_layer(release, region)
    if view not in ("image", "model", "resid"):
        raise ValueError("view must be image, model or resid")
    if view != "image":
        layer += "-" + view
    args = {"ra":format(field.ra_deg, ".7f"),
            "dec":format(field.dec_deg, ".7f"),
            "zoom":str(zoom), "layer":layer}
    if mark_target:
        args["mark"] = f"{field.ra_deg:.7f},{field.dec_deg:.7f}"
    if show_field:
        args["poly"] = field_polygon_query(overlay_field or field)
    return VIEWER_ROOT + "?" + urlencode(args)
