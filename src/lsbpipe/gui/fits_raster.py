"""Read-only raster preparation for the interactive original-FITS workbench.

All WCS registration and strict no-padding cutouts are delegated to the
already tested science module. Renderings are display-only 8-bit RGB;
the original float/integer FITS samples remain available for readout.
No Qt imports and no modification of original data.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np

from lsbpipe.surveys.legacy import SkyField, BrickHit
from lsbpipe.survey import LEGACY_BITS, maskbits_to_uint
from lsbpipe.surveys.viewer import field_polygon
from lsbpipe.surveys.qa_plots import (
    _load_four_cutouts, _circle_on_sky, classify_maskbits,
)
from .science_workflows import science_data_root


LAYERS = ("image", "invvar", "nexp", "maskbits")

# Explicit pixel provenance: red is an instrumental caution,
# amber is Gaia MEDIUM, blue is an SGA GALAXY warning.
OVERLAY_RGB = {
    "SATUR": (232, 71, 67),
    "MEDIUM": (249, 183, 63),
    "GALAXY": (77, 159, 230),
}


def _stretch(data: np.ndarray, *, kind: str, contrast: str) -> np.ndarray:
    """Convert a 2D layer to UINT8 with explicit finite/positive rules."""
    if contrast not in ("standard", "faint"):
        raise ValueError("Contrast must be standard or faint")
    values = np.asarray(data)
    if values.ndim != 2:
        raise ValueError("FITS display requires a 2D layer")
    if kind == "maskbits":
        raise ValueError("MASKBITS must use categorical color, not flux stretch")
    arr = values.astype(np.float64, copy=False)
    valid = np.isfinite(arr)
    if kind == "invvar":
        valid &= arr > 0
    if kind == "nexp":
        valid &= arr >= 0
    output = np.zeros(arr.shape, dtype=np.uint8)
    if not np.any(valid):
        return output
    selected = arr[valid]
    # The integer NEXP map retains linear value meaning; its black = zero
    # is not a star-formation or depth decision.
    if kind == "nexp":
        hi = float(np.max(selected))
        if hi > 0:
            output[valid] = np.clip(
                np.rint(255. * selected / hi), 0, 255).astype(np.uint8)
        return output
    if kind == "invvar":
        arr = np.zeros(arr.shape, dtype=np.float64)
        arr[valid] = np.log10(selected)
        selected = arr[valid]
    # Deterministic sampling for large cutouts. Image is pre-existing
    # coadd pixel data; these display percentiles are NOT physical sky.
    step = max(1, selected.size // 250000)
    sample = selected[::step]
    if kind == "image":
        low, high = np.percentile(
            sample, (2., 98.5) if contrast == "faint" else (1., 99.7))
    else:
        low, high = np.percentile(sample, (3., 97.))
    if not np.isfinite(low) or not np.isfinite(high):
        return output
    if high <= low:
        if high == low:
            output[valid] = 128
        return output
    x = np.clip((arr[valid] - low) / (high - low), 0., 1.)
    if kind == "image":
        strength = 24. if contrast == "faint" else 7.
        x = np.arcsinh(strength * x) / np.arcsinh(strength)
    output[valid] = np.rint(255. * x).astype(np.uint8)
    return output




def rgb_for_layer(data: np.ndarray, kind: str, band: str,
                  contrast: str = "standard") -> np.ndarray:
    """Return display RGB in FITS row order; GUI must invert y for screen."""
    if kind not in LAYERS:
        raise ValueError("Unsupported FITS layer")
    if kind == "maskbits":
        category = classify_maskbits(data, band)
        palette = np.asarray([
            (49, 57, 68),    # zero bits
            (76, 141, 206),  # SGA galaxy
            (225, 170, 69),  # other bit incl Gaia
            (216, 77, 70),   # saturation/allmask
        ], dtype=np.uint8)
        return palette[category]
    gray = _stretch(data, kind=kind, contrast=contrast)
    if kind == "image":
        return np.repeat(gray[:, :, None], 3, axis=2)
    if kind == "nexp":
        # Single-hue sequential map. Black always means NEXP=0.
        return np.stack(
            (gray // 3, gray * (0.65), gray), axis=-1).astype(np.uint8)
    return np.stack(
        (gray * 0.20, gray * 0.85, gray), axis=-1).astype(np.uint8)


def overlay_rgba(maskbits: np.ndarray, band: str, *,
                 show_satur: bool, show_medium: bool,
                 show_galaxy: bool, show_invalid: bool = False,
                 image: np.ndarray | None = None,
                 invvar: np.ndarray | None = None,
                 nexp: np.ndarray | None = None) -> np.ndarray:
    """Transparent, priority-ordered per-bit viewer overlay (not a mask)."""
    bits = maskbits_to_uint(maskbits)
    overlay = np.zeros((*bits.shape, 4), dtype=np.uint8)
    settings = (
        (show_galaxy, "GALAXY", OVERLAY_RGB["GALAXY"]),
        (show_medium, "MEDIUM", OVERLAY_RGB["MEDIUM"]),
    )
    for show, name, color in settings:
        if not show:
            continue
        flagged = (bits & np.uint32(1 << LEGACY_BITS[name])) != 0
        overlay[flagged, :3] = color
        overlay[flagged, 3] = 115
    if show_invalid:
        if any(layer is None for layer in (image, invvar, nexp)):
            raise ValueError("Invalidity overlay requires IMAGE, INVVAR and NEXP")
        if any(np.shape(layer) != bits.shape
               for layer in (image, invvar, nexp)):
            raise ValueError("All invalidity layers must match MASKBITS shape")
        invalid = (
            ~np.isfinite(image) |
            ~np.isfinite(invvar) | (np.asarray(invvar) <= 0) |
            ~np.isfinite(nexp) | (np.asarray(nexp) <= 0))
        overlay[invalid, :3] = (210, 57, 217)
        # Pixel-level crosshatch distinguishes missing data from faint flux.
        # This is strictly a display overlay, never a science rejection map.
        yy, xx = np.nonzero(invalid)
        overlay[yy, xx, 3] = np.where(
            (xx + yy) % 4 < 2, 170, 75).astype(np.uint8)
    if show_satur:
        selected = np.uint32(
            (1 << LEGACY_BITS["SATUR_" + band.upper()]) |
            (1 << LEGACY_BITS["ALLMASK_" + band.upper()]))
        flagged = (bits & selected) != 0
        overlay[flagged, :3] = OVERLAY_RGB["SATUR"]
        overlay[flagged, 3] = 175
    return overlay


def fits_pixel_from_scene(x: float, y: float, shape: tuple[int, int]):
    """Qt top-left coordinates -> astronomical bottom-up FITS array indices."""
    ny, nx = shape
    ix, qt_y = int(np.floor(x)), int(np.floor(y))
    if not (0 <= ix < nx and 0 <= qt_y < ny):
        return None
    return ix, ny - 1 - qt_y


def _world_outline(wcs, points):
    sky = np.asarray(points, dtype=np.float64)
    xs, ys = wcs.all_world2pix(sky[:, 0], sky[:, 1], 0)
    if not np.all(np.isfinite(xs)) or not np.all(np.isfinite(ys)):
        raise ValueError("Nonfinite WCS outline")
    return tuple((float(x), float(y)) for x, y in zip(xs, ys))


def prepare_fits_explorer(*, directory: str | Path,
                          field: SkyField, hit: BrickHit, band: str,
                          target_radius_arcsec: float = 35.,
                          sky_inner_arcsec: float = 65.,
                          sky_outer_arcsec: float = 110.) -> dict:
    """Load strict original quartet from disk in the worker thread.

    Return raw sampled arrays plus registered overlays. The GUI creates
    QImage/QPixmap and reads pixel samples solely on the main thread.
    """
    if not 0 < target_radius_arcsec <= sky_inner_arcsec < sky_outer_arcsec:
        raise ValueError("Invalid radial screen bounds")
    root = science_data_root(directory, hit=hit, band=band)
    data, wcs, indices = _load_four_cutouts(
        root, field=field, release=hit.release, region=hit.region,
        brick=hit.name, band=band)
    outlines = {
        "square": _world_outline(wcs, field_polygon(field)),
        "target": _world_outline(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, target_radius_arcsec)),
        "sky_inner": _world_outline(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, sky_inner_arcsec)),
        "sky_outer": _world_outline(wcs, _circle_on_sky(
            field.ra_deg, field.dec_deg, sky_outer_arcsec)),
    }
    # Fresh arrays are copies from the cutout reader, not memmaps tied to
    # closed FITS handles. The render cache is merely display data.
    previews = {
        kind: rgb_for_layer(values, kind, band)
        for kind, values in data.items()
    }
    return {
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "data": data,
        "previews": previews,
        "wcs": wcs,
        "outlines": outlines,
        "hdu_indices": indices,
        "field": field,
        "band": band,
        "source": f"{hit.release.upper()} {hit.region} · {hit.name} · {band}",
        "data_root": str(root.resolve()),
        "pixel_shape": tuple(data["image"].shape),
    }
