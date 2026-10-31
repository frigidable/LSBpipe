"""P3 DR9 north/south *paired* comparison on a common native WCS grid.

No image stacking, differential photometry, reprojection, or statistical
independence assumption. Fail if WCS grids differ, rather than comparing
unrelated pixel indices. Only original FITS files are read.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import json
import math

import numpy as np

from .legacy import SkyField, BrickHit, product_for
from .coverage import _science_hdu_index
from .square_coverage import _project_tan
from .viewer import field_polygon
from lsbpipe.survey import LEGACY_BITS, _check_wcs_compatible, maskbits_to_uint

KINDS = ("image", "invvar", "nexp", "maskbits")


def _four_original_paths(directory: Path, region: str, brick: str, band: str):
    hit = BrickHit("dr9", region, brick, 0., 0., 0., 0., {band: 1})
    result = {
        kind: Path(directory) / product_for(
            hit, kind, None if kind == "maskbits" else band).relative_path
        for kind in KINDS
    }
    missing = [str(p) for p in result.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "Required original DR9 FITS missing: " + ", ".join(missing))
    return result


def _pairwise_square_arrays(north: dict, south: dict, wcs, *,
                            field: SkyField, band: str, chunk_rows: int = 128):
    """Counts on precisely the same sky pixel centers; no interpolation.

    The mask truth table is over the exact common square, not the full
    3600² source brick. Independent PHOTO/sky conclusions are out of scope.
    """
    if band not in ("g", "r", "z"):
        raise ValueError("DR9 north/south common bands are g, r and z")
    if not isinstance(chunk_rows, int) or not 1 <= chunk_rows <= 1024:
        raise ValueError("chunk_rows must be integer in 1..1024")
    shape = north["image"].shape
    if len(shape) != 2 or any(
        k not in d or d[k].shape != shape for d in (north, south)
        for k in KINDS):
        raise ValueError("Both quartets must have the same 2D FITS shape")
    ny, nx = shape
    square = np.asarray(field_polygon(field, subdivisions=8))
    px, py = wcs.all_world2pix(square[:, 0], square[:, 1], 0)
    if not all(np.all(np.isfinite(v)) for v in (px, py)):
        raise ValueError("Nonfinite sky boundary positions")
    if not (np.all(px >= 0) and np.all(py >= 0) and
            np.all(px <= nx - 1) and np.all(py <= ny - 1)):
        raise ValueError(
            "Selected square is not fully inside both co-registered bricks")
    xlo = max(0, int(np.floor(np.min(px))) - 2)
    xhi = min(nx, int(np.ceil(np.max(px))) + 3)
    ylo = max(0, int(np.floor(np.min(py))) - 2)
    yhi = min(ny, int(np.ceil(np.max(py))) + 3)
    half = math.tan(math.radians(field.width_arcmin / 120.))
    sat = np.uint32(1 << LEGACY_BITS["SATUR_" + band.upper()])
    allmask = np.uint32(1 << LEGACY_BITS["ALLMASK_" + band.upper()])
    named = {
        k: np.uint32(1 << LEGACY_BITS[k])
        for k in ("MEDIUM", "GALAXY", "BRIGHT")
    }
    counted = 0
    mask_counts = {
        side: {"satur_only": 0, "allmask_only": 0, "both": 0,
               "either": 0, "other_quality_only": 0,
               "invalid_total": 0, "valid_total": 0,
               "median": {k: 0 for k in named}}
        for side in ("north", "south")
    }
    joint_valid = {
        "both": 0, "north_only": 0, "south_only": 0, "neither": 0
    }
    pair_flags = {
        "MEDIUM": {"both": 0, "north_only": 0, "south_only": 0},
        "GALAXY": {"both": 0, "north_only": 0, "south_only": 0},
        "SATUR_" + band.upper(): {
            "both": 0, "north_only": 0, "south_only": 0},
        "ALLMASK_" + band.upper(): {
            "both": 0, "north_only": 0, "south_only": 0},
    }
    nexp_pairs = {"south_gt_north": 0, "equal": 0,
                  "north_gt_south": 0, "both_positive": 0,
                  "north_positive": 0, "south_positive": 0}
    ivar_pairs = {"south_gt_north": 0, "equal": 0,
                  "north_gt_south": 0, "both_positive": 0}
    sum_nexp = {"north": 0., "south": 0.}
    for ystart in range(ylo, yhi, chunk_rows):
        yend = min(ystart + chunk_rows, yhi)
        xx, yy = np.meshgrid(
            np.arange(xlo, xhi, dtype=float),
            np.arange(ystart, yend, dtype=float))
        ra, dec = wcs.all_pix2world(xx, yy, 0)
        u, v, denom = _project_tan(ra, dec,
                                   field.ra_deg, field.dec_deg)
        inside = ((denom > 0) & (np.abs(u) <= half)
                  & (np.abs(v) <= half))
        n = int(np.count_nonzero(inside))
        if n == 0:
            continue
        counted += n
        box = (slice(ystart, yend), slice(xlo, xhi))
        masks, valids, nexps, ivars = {}, {}, {}, {}
        for side, data in (("north", north), ("south", south)):
            image, ivar, nexp = (
                data[k][box] for k in ("image", "invvar", "nexp"))
            bits = maskbits_to_uint(data["maskbits"][box])
            sat_flag = (bits & sat) != 0
            all_flag = (bits & allmask) != 0
            quality = (np.isfinite(image) & np.isfinite(ivar)
                       & (ivar > 0) & np.isfinite(nexp) & (nexp > 0))
            valid = quality & ~sat_flag & ~all_flag
            valids[side] = valid
            masks[side] = bits
            nexps[side], ivars[side] = nexp, ivar
            ctr = mask_counts[side]
            for key, cond in (
                ("satur_only", sat_flag & ~all_flag),
                ("allmask_only", all_flag & ~sat_flag),
                ("both", sat_flag & all_flag),
                ("either", sat_flag | all_flag),
                ("other_quality_only", ~quality & ~sat_flag & ~all_flag),
                ("invalid_total", ~valid),
                ("valid_total", valid),
            ):
                ctr[key] += int(np.count_nonzero(inside & cond))
            for name, bit in named.items():
                ctr["median"][name] += int(
                    np.count_nonzero(inside & ((bits & bit) != 0)))
            pos = inside & np.isfinite(nexp) & (nexp > 0)
            nexp_pairs[side + "_positive"] += int(np.count_nonzero(pos))
            sum_nexp[side] += float(np.sum(nexp[pos], dtype=np.float64))
        v_n, v_s = valids["north"], valids["south"]
        for key, cond in (
            ("both", v_n & v_s),
            ("north_only", v_n & ~v_s),
            ("south_only", ~v_n & v_s),
            ("neither", ~v_n & ~v_s),
        ):
            joint_valid[key] += int(np.count_nonzero(inside & cond))
        for name, bit in (
            ("MEDIUM", named["MEDIUM"]),
            ("GALAXY", named["GALAXY"]),
            ("SATUR_" + band.upper(), sat),
            ("ALLMASK_" + band.upper(), allmask),
        ):
            bn, bs = (masks[side] & bit) != 0 for side in ("north", "south")
            for key, cond in (
                ("both", bn & bs),
                ("north_only", bn & ~bs),
                ("south_only", ~bn & bs),
            ):
                pair_flags[name][key] += int(
                    np.count_nonzero(inside & cond))
        en = np.isfinite(nexps["north"]) & (nexps["north"] > 0)
        es = np.isfinite(nexps["south"]) & (nexps["south"] > 0)
        both = inside & en & es
        nexp_pairs["both_positive"] += int(np.count_nonzero(both))
        for key, cond in (
            ("south_gt_north", nexps["south"] > nexps["north"]),
            ("north_gt_south", nexps["north"] > nexps["south"]),
            ("equal", nexps["north"] == nexps["south"]),
        ):
            nexp_pairs[key] += int(np.count_nonzero(both & cond))
        en = np.isfinite(ivars["north"]) & (ivars["north"] > 0)
        es = np.isfinite(ivars["south"]) & (ivars["south"] > 0)
        both = inside & en & es
        ivar_pairs["both_positive"] += int(np.count_nonzero(both))
        for key, cond in (
            ("south_gt_north", ivars["south"] > ivars["north"]),
            ("north_gt_south", ivars["north"] > ivars["south"]),
            ("equal", ivars["north"] == ivars["south"]),
        ):
            ivar_pairs[key] += int(np.count_nonzero(both & cond))
    if counted == 0:
        raise ValueError("No selected pixel centers in square")
    result = {
        "schema": "lsbpipe.dr9-region-comparison/0.1",
        "status": "DIAGNOSTIC_NOT_ADOPTED",
        "field": {"ra_deg": field.ra_deg, "dec_deg": field.dec_deg,
                  "width_arcmin": field.width_arcmin},
        "band": band,
        "sampling": "same original WCS grid; common TAN sky square",
        "pixels": counted,
        "per_region": mask_counts,
        "paired_instrumental_validity": joint_valid,
        "paired_mask_flags": pair_flags,
        "paired_nexp": nexp_pairs,
        "mean_nexp_positive": {
            s: sum_nexp[s] / nexp_pairs[s + "_positive"]
            if nexp_pairs[s + "_positive"] else None
            for s in ("north", "south")
        },
        "paired_inverse_variance": ivar_pairs,
        "warnings": [
            "North and South are survey reductions; not independent exposures",
            "NEXP and IVAR are descriptive, not a PSF- or sky-calibrated depth",
            "Comparing IVAR values assumes same native nominal flux units",
            "PSF, background, correlated noise and full photometric errors "
            "must be evaluated separately",
            "SGA GALAXY and Gaia MEDIUM masks are warnings, not hard exclusions",
        ],
    }
    for side in ("north", "south"):
        region = result["per_region"][side]
        region["instrumentally_usable_fraction"] = (
            region["valid_total"] / counted)
        # Historical key retained only inside this new report, never
        # replace an earlier pilot-square report.
        region["named_mask_counts"] = region.pop("median")
    return result


def compare_original_regions(north_directory: Path, south_directory: Path,
                             *, field: SkyField, brick: str, band: str):
    """Strictly compare native original DR9 north/south coadds at pixel level.

    The two nominal brick grids must have identical shape, and five
    celestial WCS tie-points must match within 1e-3 pixel. A mismatch
    requires a separately reviewed reprojection experiment, NOT silently
    comparing mismatched pixel indices.
    """
    from astropy.io import fits
    from astropy.wcs import WCS

    if band not in ("g", "r", "z"):
        raise ValueError("Only common DR9 north/south g/r/z bands are supported")
    all_paths = {
        "north": _four_original_paths(north_directory, "north", brick, band),
        "south": _four_original_paths(south_directory, "south", brick, band),
    }
    with ExitStack() as stack:
        images, wcs, hdu_indices, units = {}, {}, {}, {}
        for side in ("north", "south"):
            hdul = {
                k: stack.enter_context(fits.open(p, memmap=True))
                for k, p in all_paths[side].items()
            }
            hdu_indices[side] = {
                k: _science_hdu_index(hdul[k], k) for k in KINDS
            }
            images[side] = {
                k: hdul[k][hdu_indices[side][k]].data for k in KINDS
            }
            wcs[side] = {
                k: WCS(hdul[k][hdu_indices[side][k]].header).celestial
                for k in KINDS
            }
            shape = images[side]["image"].shape
            for kind in KINDS:
                if images[side][kind].shape != shape:
                    raise ValueError(f"{side}/{kind}: inconsistent pixel shape")
                _check_wcs_compatible(
                    wcs[side]["image"], wcs[side][kind],
                    shape, f"{side} {kind}")
            units[side] = {
                k: hdul[k][hdu_indices[side][k]].header.get("BUNIT")
                for k in KINDS
            }
        if images["north"]["image"].shape != images["south"]["image"].shape:
            raise ValueError("North and South nominal brick shapes differ")
        _check_wcs_compatible(
            wcs["north"]["image"], wcs["south"]["image"],
            images["north"]["image"].shape, "North/South")
        result = _pairwise_square_arrays(
            images["north"], images["south"], wcs["north"]["image"],
            field=field, band=band)
    result["brick"] = brick
    result["native_wcs_alignment"] = "PASSED_0.001_PIX_TIE_POINTS"
    result["hdu_indices"] = hdu_indices
    result["header_units"] = units
    result["original_files"] = {
        side: {kind: str(path.resolve())
               for kind, path in paths.items()}
        for side, paths in all_paths.items()
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare original DR9 north/south FITS; no stacking")
    parser.add_argument("--north-directory", required=True, type=Path)
    parser.add_argument("--south-directory", required=True, type=Path)
    parser.add_argument("--brick", required=True)
    parser.add_argument("--band", required=True, choices=("g", "r", "z"))
    parser.add_argument("--ra", type=float, required=True)
    parser.add_argument("--dec", type=float, required=True)
    parser.add_argument("--width-arcmin", type=float, default=5.)
    parser.add_argument("--output", required=True, type=Path)
    a = parser.parse_args(argv)
    report = compare_original_regions(
        a.north_directory, a.south_directory,
        field=SkyField(a.ra, a.dec, a.width_arcmin),
        brick=a.brick, band=a.band)
    out = a.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Saved paired north/south diagnostic: {out}")
    print(f"Matched original WCS and {report['pixels']} common sky pixels")
    print("DIAGNOSTIC_NOT_ADOPTED; no stacking or science photometry")


if __name__ == "__main__":
    main()
