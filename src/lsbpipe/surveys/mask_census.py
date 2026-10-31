"""Footprint-aware diagnostic census of the original Legacy science quartet.

The original FITS are not modified. Every denominator counts actual native
pixel centers. No catalog warning is silently promoted to a scientific veto.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from lsbpipe.survey import LEGACY_BITS, maskbits_to_uint
from .legacy import SkyField
from .qa_plots import _load_four_cutouts
from .square_coverage import _project_tan


SCOPES = ("cutout", "square", "target_in_square", "sky_annulus_in_square")
CATEGORIES = ("clean", "other_bit_not_excluded", "satur", "allmask", "bad_numeric")
PAIR_FLAGS = ("SATUR", "ALLMASK", "BRIGHT", "MEDIUM", "GALAXY")


def _ratio(count: int, total: int) -> float | None:
    return count / total if total else None


def _separation_rad(ra, dec, ra0: float, dec0: float):
    """Stable haversine separation, including RA wrap and near-pole fields."""
    ra = np.deg2rad(ra)
    dec = np.deg2rad(dec)
    d0 = math.radians(dec0)
    a0 = math.radians(ra0)
    h = (np.sin((dec - d0) / 2.)**2 +
         math.cos(d0) * np.cos(dec) * np.sin((ra - a0) / 2.)**2)
    return 2. * np.arcsin(np.sqrt(np.clip(h, 0., 1.)))


def mask_census_from_arrays(
    image, invvar, nexp, maskbits, wcs, *,
    field: SkyField, band: str,
    target_radius_arcsec: float = 35.,
    sky_inner_arcsec: float = 65.,
    sky_outer_arcsec: float = 110.,
    chunk_rows: int = 128,
) -> dict:
    """Count exact native pixels in four explicitly defined WCS footprints.

    The target and sky-annulus scopes are *intersections* with the TAN square;
    they cannot be interpreted as complete circular footprints when clipped.
    All inclusive flag/overlap counts have the identical scope denominator.
    """
    if band not in ("g", "r", "i", "z"):
        raise ValueError("Unsupported band")
    if not (0 < target_radius_arcsec <= sky_inner_arcsec < sky_outer_arcsec):
        raise ValueError("Require 0 < target <= sky inner < sky outer")
    if not isinstance(chunk_rows, int) or not 1 <= chunk_rows <= 2048:
        raise ValueError("chunk_rows must be integer in [1,2048]")
    data = {key: np.asarray(value) for key, value in
            (("image", image), ("invvar", invvar), ("nexp", nexp),
             ("maskbits", maskbits))}
    shape = data["image"].shape
    if len(shape) != 2 or min(shape) == 0 or any(
            a.shape != shape for a in data.values()):
        raise ValueError("Original quartet must share one nonempty 2D grid")
    raw_bits = data["maskbits"]
    if raw_bits.dtype.kind not in "iu" or raw_bits.dtype.itemsize not in (2, 4):
        raise ValueError("MASKBITS requires signed/unsigned 16- or 32-bit values")
    if not getattr(wcs, "has_celestial", False) or getattr(
            wcs, "pixel_n_dim", None) != 2:
        raise ValueError("Expected a 2D celestial WCS")

    mask_names = tuple(LEGACY_BITS)
    named = {key: np.uint32(1 << value) for key, value in LEGACY_BITS.items()}
    known_mask = np.uint32(sum(int(flag) for flag in named.values()))
    hard_sat = named["SATUR_" + band.upper()]
    hard_all = named["ALLMASK_" + band.upper()]
    pair_names = {
        "SATUR": "SATUR_" + band.upper(),
        "ALLMASK": "ALLMASK_" + band.upper(),
        "BRIGHT": "BRIGHT", "MEDIUM": "MEDIUM", "GALAXY": "GALAXY",
    }
    pairs = tuple((a, b) for i, a in enumerate(PAIR_FLAGS)
                  for b in PAIR_FLAGS[i + 1:])
    pair_labels = tuple(a + "&" + b for a, b in pairs)
    reasons = ("image_nonfinite", "invvar_nonpositive_or_nonfinite",
               "nexp_nonpositive_or_nonfinite")
    overlaps = ("satur_and_allmask", "numeric_and_hard",
                "numeric_and_warning", "hard_and_warning")

    accum = {}
    for scope in SCOPES:
        accum[scope] = {
            "pixels": 0,
            "numeric": {name: 0 for name in
                        (*reasons, "invalid_union", "finite_image",
                         "positive_invvar", "positive_nexp")},
            "instrumental": {name: 0 for name in
                             ("satur", "allmask", "hard_union", "usable")},
            "quality_categories": {name: 0 for name in CATEGORIES},
            "maskbits": {name: 0 for name in mask_names},
            "other_unknown_bits_pixels": 0,
            "pair_overlaps": {name: 0 for name in pair_labels},
            "cross_overlaps": {name: 0 for name in overlaps},
        }

    ny, nx = shape
    half = math.tan(math.radians(field.width_arcmin / 120.))
    radii = np.deg2rad(
        np.asarray([target_radius_arcsec, sky_inner_arcsec,
                    sky_outer_arcsec], dtype=float) / 3600.)

    for ylo in range(0, ny, chunk_rows):
        yhi = min(ny, ylo + chunk_rows)
        # Pixel-center coordinates, never interpolated or reprojected.
        xx, yy = np.meshgrid(np.arange(nx, dtype=float),
                             np.arange(ylo, yhi, dtype=float))
        ra, dec = wcs.all_pix2world(xx, yy, 0)
        if not (np.all(np.isfinite(ra)) and np.all(np.isfinite(dec))):
            raise ValueError("Nonfinite WCS sky coordinates in FITS cutout")
        east, north, denom = _project_tan(
            ra, dec, field.ra_deg, field.dec_deg)
        square = (np.isfinite(east) & np.isfinite(north) &
                  (denom > 0) & (np.abs(east) <= half) &
                  (np.abs(north) <= half))
        distance = _separation_rad(ra, dec, field.ra_deg, field.dec_deg)
        membership = {
            "cutout": np.ones(xx.shape, dtype=bool),
            "square": square,
            "target_in_square": square & (distance <= radii[0]),
            "sky_annulus_in_square": square & (distance >= radii[1]) &
                                     (distance <= radii[2]),
        }

        sl = slice(ylo, yhi)
        bits = maskbits_to_uint(raw_bits[sl])
        good_image = np.isfinite(data["image"][sl])
        good_invvar = (np.isfinite(data["invvar"][sl]) &
                       (data["invvar"][sl] > 0))
        good_nexp = (np.isfinite(data["nexp"][sl]) &
                     (data["nexp"][sl] > 0))
        bad_numeric = ~(good_image & good_invvar & good_nexp)
        satur = (bits & hard_sat) != 0
        allmask = (bits & hard_all) != 0
        hard = satur | allmask
        warning = (bits & np.uint32(~int(hard_sat | hard_all) & 0xFFFFFFFF)) != 0
        usable = ~bad_numeric & ~hard
        # Same priority as GUI quality_map and overlay_rgba:
        # numeric > ALLMASK > SATUR > other bit > zero bit.
        classes = np.where(bits != 0, 1, 0).astype(np.uint8)
        classes[satur] = 2
        classes[allmask] = 3
        classes[bad_numeric] = 4
        unknown = (bits & np.uint32(~int(known_mask) & 0xFFFFFFFF)) != 0
        present = {key: (bits & value) != 0 for key, value in named.items()}
        pair_mask = {name: present[pair_names[name]] for name in PAIR_FLAGS}

        for scope, selected in membership.items():
            n = int(np.count_nonzero(selected))
            stats = accum[scope]
            stats["pixels"] += n
            if not n:
                continue
            nstat = stats["numeric"]
            for name, values in (
                ("image_nonfinite", ~good_image),
                ("invvar_nonpositive_or_nonfinite", ~good_invvar),
                ("nexp_nonpositive_or_nonfinite", ~good_nexp),
                ("invalid_union", bad_numeric),
                ("finite_image", good_image),
                ("positive_invvar", good_invvar),
                ("positive_nexp", good_nexp),
            ):
                nstat[name] += int(np.count_nonzero(selected & values))
            istat = stats["instrumental"]
            for name, values in (
                ("satur", satur), ("allmask", allmask),
                ("hard_union", hard), ("usable", usable),
            ):
                istat[name] += int(np.count_nonzero(selected & values))
            frequencies = np.bincount(classes[selected], minlength=5)
            for j, name in enumerate(CATEGORIES):
                stats["quality_categories"][name] += int(frequencies[j])
            stats["other_unknown_bits_pixels"] += int(
                np.count_nonzero(selected & unknown))
            for name, values in present.items():
                stats["maskbits"][name] += int(np.count_nonzero(selected & values))
            for a, b in pairs:
                stats["pair_overlaps"][a + "&" + b] += int(
                    np.count_nonzero(selected & pair_mask[a] & pair_mask[b]))
            for name, values in (
                ("satur_and_allmask", satur & allmask),
                ("numeric_and_hard", bad_numeric & hard),
                ("numeric_and_warning", bad_numeric & warning),
                ("hard_and_warning", hard & warning),
            ):
                stats["cross_overlaps"][name] += int(
                    np.count_nonzero(selected & values))

    # Only emit derived fractions after all per-row counts have accumulated.
    for stats in accum.values():
        n = stats["pixels"]
        stats["quality_fractions"] = {
            key: _ratio(count, n)
            for key, count in stats["quality_categories"].items()}
        stats["usable_fraction"] = _ratio(stats["instrumental"]["usable"], n)
        stats["maskbit_fractions"] = {
            key: _ratio(count, n) for key, count in stats["maskbits"].items()}

        assert sum(stats["quality_categories"].values()) == n
        assert (stats["instrumental"]["usable"] +
                stats["numeric"]["invalid_union"] +
                stats["instrumental"]["hard_union"] -
                stats["cross_overlaps"]["numeric_and_hard"] == n)

    return {
        "schema": "lsbpipe.footprint-mask-census/0.1",
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "field": {"ra_deg": field.ra_deg, "dec_deg": field.dec_deg,
                  "width_arcmin": field.width_arcmin},
        "band": band,
        "shape": [ny, nx],
        "radii_arcsec": {"target": target_radius_arcsec,
                         "sky_inner": sky_inner_arcsec,
                         "sky_outer": sky_outer_arcsec},
        "scope_definitions": {
            "cutout": "All original native FITS cutout pixel centers",
            "square": "Original pixel centers inside requested ICRS TAN square",
            "target_in_square": "Spherical target circle intersected with TAN square",
            "sky_annulus_in_square":
                "Spherical sky annulus intersected with TAN square",
        },
        "pixel_unit": "native pixel centers; counts overlap unless categorical",
        "quality_priority": list(reversed(CATEGORIES)),
        "scope_counts": accum,
        "warnings": [
            "Diagnostic only: original FITS, no sky/PSF/noise-covariance model",
            "Masks are not source identifications or adopted science exclusions",
            "GALAXY and Gaia flags may cover the target; do not auto-veto",
            "Circular/annular scopes are clipped to the square; no aperture completeness implied",
            "Overlapping raw bit counts must never be summed as disjoint populations",
        ],
    }


def inspect_local_mask_census(
    directory: Path, *, field: SkyField, release: str,
    region: str, brick: str, band: str,
    target_radius_arcsec: float = 35.,
    sky_inner_arcsec: float = 65.,
    sky_outer_arcsec: float = 110.,
) -> dict:
    """Use the established strict WCS-registered no-padding FITS loader."""
    data, wcs, hdu_indices = _load_four_cutouts(
        Path(directory), field=field, release=release, region=region,
        brick=brick, band=band)
    report = mask_census_from_arrays(
        data["image"], data["invvar"], data["nexp"], data["maskbits"],
        wcs, field=field, band=band,
        target_radius_arcsec=target_radius_arcsec,
        sky_inner_arcsec=sky_inner_arcsec,
        sky_outer_arcsec=sky_outer_arcsec)
    report["source"] = {"release": release, "region": region, "brick": brick,
                        "hdu_indices": hdu_indices}
    return report


def save_mask_census(report: dict, output: Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
