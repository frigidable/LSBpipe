"""P3 spatial catalog of original Legacy MASKBITS footprints.

Masks are sampled in the user-selected square on the exact ICRS tangent
plane. The catalog describes connected mask regions, not astronomical
object detections, and does not by itself attribute them to Gaia/SGA
sources. Outputs are diagnostic only; no masks or FITS are modified.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math

import numpy as np
from scipy.ndimage import label, find_objects
from astropy.coordinates import SkyCoord
from astropy.wcs.utils import proj_plane_pixel_scales
import astropy.units as u

from .legacy import SkyField
from .qa_plots import _load_four_cutouts
from .square_coverage import _project_tan
from lsbpipe.survey import LEGACY_BITS, maskbits_to_uint

TRACKED_BITS = ("SATUR_R", "MEDIUM", "GALAXY")


@dataclass(frozen=True)
class MaskComponent:
    component_id: int
    pixels: int
    centroid_x: float
    centroid_y: float
    centroid_ra_deg: float
    centroid_dec_deg: float
    angular_separation_arcsec: float
    target_circle_pixels: int
    sky_annulus_pixels: int
    touches_square_boundary: bool
    bounding_box_xyxy: tuple[int, int, int, int]


def mask_components_from_array(
    maskbits, wcs, *, field: SkyField, band: str = "r",
    target_radius_arcsec: float = 35.,
    sky_inner_arcsec: float = 65.,
    sky_outer_arcsec: float = 110.,
    max_components_per_bit: int = 20,
) -> dict:
    """Return connected (8-neighbor) components inside the requested square.

    Pixel membership uses actual sky coordinates, including RA wrap. Circles
    are independently sampled with exact SkyCoord angular separations.
    These circles are diagnostic geometry; they need not match the older
    Jacobian-approximated circular QA at a pixel-level boundary.
    """
    if not 0 < target_radius_arcsec <= sky_inner_arcsec < sky_outer_arcsec:
        raise ValueError("Invalid target/sky radii")
    if not 1 <= max_components_per_bit <= 200:
        raise ValueError("max_components_per_bit must be in 1..200")
    if band not in ("g", "r", "i", "z"):
        raise ValueError("Invalid band")
    if not wcs.has_celestial or wcs.pixel_n_dim != 2:
        raise ValueError("A 2D celestial WCS is required")
    bits = maskbits_to_uint(maskbits)
    if bits.ndim != 2:
        raise ValueError("Expected 2D MASKBITS raster")
    center = SkyCoord(field.ra_deg*u.deg, field.dec_deg*u.deg, frame="icrs")
    half = math.tan(math.radians(field.width_arcmin / 120.))
    scales = proj_plane_pixel_scales(wcs) * 3600.
    edge_tol = math.radians(2 * float(np.max(scales)) / 3600.)

    result = {
        "schema": "lsbpipe.mask-components/0.1",
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "field": {"ra_deg": field.ra_deg, "dec_deg": field.dec_deg,
                  "side_arcmin": field.width_arcmin},
        "band": band,
        "connectivity": "8-neighbor within selected TAN search square",
        "target_circle_arcsec": target_radius_arcsec,
        "sky_annulus_arcsec": [sky_inner_arcsec, sky_outer_arcsec],
        "warnings": [
            "A connected mask footprint is not a catalog source identification",
            "SGA GALAXY may mark the target itself; do not automatically mask",
            "Gaia MEDIUM does not quantify stellar PSF-wing contamination",
            "Component edges touching the square may be truncated",
            "Circle counts here use spherical angular separation, while "
            "legacy circle QA uses a local linearized WCS Jacobian",
        ],
        "bits": {},
    }
    # Only the selected bits are materialized and connected; all
    # unselected science pixels remain unchanged on disk and in memory.
    names = (
        f"SATUR_{band.upper()}", "MEDIUM", "GALAXY", "BRIGHT",
        f"ALLMASK_{band.upper()}",
    )
    for name in names:
        bit = np.uint32(1 << LEGACY_BITS[name])
        yy, xx = np.nonzero((bits & bit) != 0)
        mask = np.zeros(bits.shape, dtype=bool)
        if yy.size:
            ra, dec = wcs.all_pix2world(xx, yy, 0)
            east, north, denominator = _project_tan(
                ra, dec, field.ra_deg, field.dec_deg)
            keep = (denominator > 0) & (np.abs(east) <= half) & (
                np.abs(north) <= half)
            xx, yy = xx[keep], yy[keep]
            east, north = east[keep], north[keep]
            ra, dec = ra[keep], dec[keep]
            mask[yy, xx] = True
            separations = SkyCoord(
                ra*u.deg, dec*u.deg, frame="icrs").separation(center).arcsec
            target_member = separations <= target_radius_arcsec
            ring_member = ((separations >= sky_inner_arcsec) &
                           (separations <= sky_outer_arcsec))
            boundary = ((np.abs(east) >= half - edge_tol) |
                        (np.abs(north) >= half - edge_tol))
        else:
            target_member = ring_member = boundary = np.zeros(0, dtype=bool)
        labeled, nlabels = label(mask, structure=np.ones((3, 3), dtype=np.uint8))
        ids = labeled[yy, xx]
        sizes = np.bincount(ids, minlength=nlabels+1)
        sizes[0] = 0
        target_counts = np.bincount(
            ids, weights=target_member, minlength=nlabels+1).astype(int)
        ring_counts = np.bincount(
            ids, weights=ring_member, minlength=nlabels+1).astype(int)
        boundary_count = np.bincount(
            ids, weights=boundary, minlength=nlabels+1)
        locations = find_objects(labeled)
        ordered = sorted(range(1, nlabels+1), key=lambda k: (-sizes[k], k))
        summaries = []
        for index in ordered[:max_components_per_bit]:
            sl_y, sl_x = locations[index-1]
            sub = (labeled[sl_y, sl_x] == index)
            sy, sx = np.nonzero(sub)
            cx = float(np.mean(sx + sl_x.start))
            cy = float(np.mean(sy + sl_y.start))
            cra, cdec = wcs.all_pix2world(cx, cy, 0)
            csky = SkyCoord(cra*u.deg, cdec*u.deg, frame="icrs")
            component = MaskComponent(
                component_id=index, pixels=int(sizes[index]),
                centroid_x=cx, centroid_y=cy,
                centroid_ra_deg=float(cra) % 360.,
                centroid_dec_deg=float(cdec),
                angular_separation_arcsec=float(csky.separation(center).arcsec),
                target_circle_pixels=int(target_counts[index]),
                sky_annulus_pixels=int(ring_counts[index]),
                touches_square_boundary=bool(boundary_count[index]),
                bounding_box_xyxy=(sl_x.start, sl_y.start,
                                   sl_x.stop-1, sl_y.stop-1))
            summaries.append(asdict(component))
        result["bits"][name] = {
            "total_pixels_inside_square": int(xx.size),
            "connected_regions": int(nlabels),
            "reported_regions": len(summaries),
            "unreported_regions": max(0, nlabels-len(summaries)),
            "total_target_circle_pixels": int(np.sum(target_member)),
            "total_sky_annulus_pixels": int(np.sum(ring_member)),
            "regions": summaries,
        }
    return result


def inspect_mask_components(directory: Path, *, field: SkyField,
                            release: str, region: str, brick: str, band: str,
                            target_radius_arcsec: float = 35.,
                            sky_inner_arcsec: float = 65.,
                            sky_outer_arcsec: float = 110.) -> dict:
    """Read-only localization on the existing four co-registered FITS."""
    data, wcs, indices = _load_four_cutouts(
        directory, field=field, release=release,
        region=region, brick=brick, band=band)
    report = mask_components_from_array(
        data["maskbits"], wcs, field=field, band=band,
        target_radius_arcsec=target_radius_arcsec,
        sky_inner_arcsec=sky_inner_arcsec,
        sky_outer_arcsec=sky_outer_arcsec)
    report["original_hdu_indices"] = indices
    report["provenance"] = {
        "release": release, "region": region, "brick": brick,
        "note": "Cutout coordinates in each region are relative to the "
                "centered read-only FITS display cutout, not the full brick",
    }
    return report


def save_mask_components(report: dict, output: Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
