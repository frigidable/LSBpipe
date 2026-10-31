"""Strict DR9 north/south paired comparison: offline native WCS fixtures."""
import json
import numpy as np
import pytest

pytest.importorskip("astropy")
from astropy.wcs import WCS
from astropy.io import fits

from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for
from lsbpipe.surveys.compare_regions import (
    _pairwise_square_arrays, compare_original_regions
)


FIELD = SkyField(40.096208, 1.229250, 1.)


def grid():
    w = WCS(naxis=2)
    w.wcs.crpix = [100.5, 100.5]
    w.wcs.cdelt = [-1 / 3600., 1 / 3600.]
    w.wcs.crval = [FIELD.ra_deg, FIELD.dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return w


def arrays(nexp=2):
    shape = (200, 200)
    return {
        "image": np.ones(shape, dtype="f4"),
        "invvar": np.full(shape, 4., dtype="f4"),
        "nexp": np.full(shape, nexp, dtype=np.int16),
        "maskbits": np.zeros(shape, dtype=np.int32),
    }


def test_paired_validity_and_exact_north_south_mask_overlap():
    w = grid()
    north, south = arrays(2), arrays(5)
    north["maskbits"][100, 100] = 1 << 3    # northern SATUR
    north["maskbits"][101, 101] = 1 << 6    # northern ALLMASK
    north["maskbits"][102, 102] = (1 << 3) | (1 << 6)
    north["image"][103, 103] = np.nan      # disjoint numeric quality
    south["maskbits"][101, 101] = 1 << 3    # shared pixel, different bits
    south["maskbits"][104, 104] = 1 << 6
    north["maskbits"][105, 105] = 1 << 12
    south["maskbits"][105, 105] = 1 << 12
    north["maskbits"][106, 106] = 1 << 11
    south["maskbits"][106, 106] = 1 << 11
    report = _pairwise_square_arrays(
        north, south, w, field=FIELD, band="r", chunk_rows=33)
    assert report["pixels"] >= 3500
    n, s = report["per_region"]["north"], report["per_region"]["south"]
    assert (n["satur_only"], n["allmask_only"], n["both"]) == (1, 1, 1)
    assert n["either"] == 3
    assert n["other_quality_only"] == 1
    assert n["invalid_total"] == 4
    assert (s["satur_only"], s["allmask_only"], s["both"]) == (1, 1, 0)
    assert s["invalid_total"] == 2
    paired = report["paired_instrumental_validity"]
    assert paired["both"] == report["pixels"] - 5
    assert paired["north_only"] == 1
    assert paired["south_only"] == 3
    assert paired["neither"] == 1
    assert sum(paired.values()) == report["pixels"]
    assert report["paired_mask_flags"]["GALAXY"] == {
        "both":1,"north_only":0,"south_only":0}
    assert report["paired_mask_flags"]["MEDIUM"]["both"] == 1
    assert report["paired_mask_flags"]["SATUR_R"] == {
        "both":0,"north_only":2,"south_only":1}
    assert report["paired_mask_flags"]["ALLMASK_R"] == {
        "both":0,"north_only":2,"south_only":1}
    assert report["paired_nexp"]["south_gt_north"] == report["pixels"]
    assert report["paired_nexp"]["both_positive"] == report["pixels"]
    assert report["paired_inverse_variance"]["equal"] == report["pixels"]
    assert report["mean_nexp_positive"] == {
        "north":2.,"south":5.}
    json.dumps(report, allow_nan=False)


def test_nonoverlapping_sky_square_fails_closed():
    w = grid()
    with pytest.raises(ValueError, match="not fully inside"):
        _pairwise_square_arrays(
            arrays(), arrays(5), w,
            field=SkyField(40.096208+0.1, 1.22925, 1.), band="r")


def save_fits(root, side, *, north=False, shifted=False):
    src = arrays(2 if north else 5)
    w = grid()
    if shifted:
        w.wcs.crval[0] += 0.005
    hit = BrickHit("dr9", side, "0401p012", 40., 40.2, 1.1, 1.35,
                   {"r": 1})
    for kind, values in src.items():
        relative = product_for(
            hit, kind, None if kind == "maskbits" else "r").relative_path
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fits.HDUList([
            fits.PrimaryHDU(),
            fits.CompImageHDU(data=values, header=w.to_header())
        ]).writeto(path)


def test_native_original_fits_paired_comparison_without_reprojection(tmp_path):
    north, south = tmp_path/"north", tmp_path/"south"
    save_fits(north, "north", north=True)
    save_fits(south, "south")
    result = compare_original_regions(
        north, south, field=FIELD, brick="0401p012", band="r")
    assert result["native_wcs_alignment"] == "PASSED_0.001_PIX_TIE_POINTS"
    assert result["pixels"] >= 3500
    assert result["hdu_indices"]["north"] == {
        "image":1,"invvar":1,"nexp":1,"maskbits":1}
    assert result["mean_nexp_positive"] == {"north":2.,"south":5.}
    assert all("0401p012" in x for x in result["original_files"]["south"].values())


def test_native_wcs_mismatch_rejected_before_pixel_statistics(tmp_path):
    north, south = tmp_path/"north", tmp_path/"south"
    save_fits(north, "north", north=True)
    save_fits(south, "south", shifted=True)
    with pytest.raises(ValueError, match="North/South WCS grid mismatch"):
        compare_original_regions(
            north, south, field=FIELD, brick="0401p012", band="r")
