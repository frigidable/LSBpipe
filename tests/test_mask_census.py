"""Strict footprint census on synthetic native-pixel WCS grids."""
import json

import numpy as np
import pytest

from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.mask_census import (
    SCOPES, mask_census_from_arrays, save_mask_census)
from lsbpipe.gui.quality_map import quality_classes


def _fixture():
    pytest.importorskip("astropy")
    from astropy.wcs import WCS
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [4., 4.]
    wcs.wcs.cdelt = [-1. / 3600., 1. / 3600.]
    wcs.wcs.crval = [359.9995, 0.2]  # RA wraps through 0 degrees
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    shape = (7, 7)
    data = {
        "image": np.zeros(shape, dtype="f4"),
        "invvar": np.ones(shape, dtype="f4"),
        "nexp": np.full(shape, 3, dtype="i2"),
        "maskbits": np.zeros(shape, dtype="i4"),
    }
    bit = data["maskbits"]
    bit[3, 3] = (1 << 3) | (1 << 12)    # SATUR_R & GALAXY
    bit[3, 4] = (1 << 6) | (1 << 11)    # ALLMASK_R & MEDIUM
    bit[3, 2] = (1 << 3) | (1 << 6)     # SATUR_R & ALLMASK_R
    bit[2, 3] = (1 << 1) | (1 << 12)    # BRIGHT & GALAXY
    bit[3, 5] = (1 << 3)                 # in sky annulus
    bit[1, 3] = (1 << 11)                # in sky annulus
    bit[2, 1] = (1 << 12)                # in sky annulus
    bit[0, 0] = 1 << 21                 # unknown flag, cutout only
    data["invvar"][3, 4] = 0.
    field = SkyField(359.9995, 0.2, 5. / 60.)  # TAN square ±2.5"
    return data, wcs, field


def _run(data, wcs, field, *, rows=3, band="r"):
    return mask_census_from_arrays(
        data["image"], data["invvar"], data["nexp"], data["maskbits"],
        wcs, field=field, band=band,
        target_radius_arcsec=1.1,
        sky_inner_arcsec=1.8,
        sky_outer_arcsec=2.45, chunk_rows=rows)


def test_exact_four_scopes_and_inclusive_flag_overlap_with_ra_wrap(tmp_path):
    data, wcs, field = _fixture()
    before = {key: a.copy() for key, a in data.items()}
    report = _run(data, wcs, field, rows=2)
    assert report["status"] == "DIAGNOSTIC_NOT_ADOPTED"
    assert tuple(report["scope_counts"]) == SCOPES
    counts = report["scope_counts"]
    assert [counts[key]["pixels"] for key in SCOPES] == [49, 25, 5, 12]
    sq = counts["square"]
    assert sq["maskbits"]["SATUR_R"] == 3
    assert sq["maskbits"]["ALLMASK_R"] == 2
    assert sq["pair_overlaps"]["SATUR&ALLMASK"] == 1
    assert sq["pair_overlaps"]["SATUR&GALAXY"] == 1
    assert sq["numeric"]["invalid_union"] == 1
    assert sq["cross_overlaps"]["numeric_and_hard"] == 1
    assert sq["instrumental"]["hard_union"] == 4
    assert sq["instrumental"]["usable"] == 21
    assert sq["usable_fraction"] == 21 / 25
    target = counts["target_in_square"]
    assert target["pixels"] == 5
    assert target["instrumental"]["usable"] == 2
    assert target["quality_categories"] == {
        "clean": 1, "other_bit_not_excluded": 1,
        "satur": 1, "allmask": 1, "bad_numeric": 1}
    assert counts["sky_annulus_in_square"]["instrumental"]["usable"] == 11
    assert counts["cutout"]["other_unknown_bits_pixels"] == 1
    assert sq["other_unknown_bits_pixels"] == 0
    for scope in SCOPES:
        item = counts[scope]
        assert sum(item["quality_categories"].values()) == item["pixels"]
    for name, a in data.items():
        np.testing.assert_array_equal(a, before[name])
    path = save_mask_census(report, tmp_path / "report.json")
    assert json.loads(path.read_text("utf-8")) == report


def test_region_aware_census_consistent_with_gui_quality_class_map():
    data, wcs, field = _fixture()
    result = _run(data, wcs, field, rows=1)
    classes = quality_classes(data, "r")
    values = result["scope_counts"]["cutout"]["quality_categories"]
    for number, name in enumerate(("clean", "other_bit_not_excluded", "satur",
                                   "allmask", "bad_numeric")):
        assert values[name] == int(np.count_nonzero(classes == number))
    assert _run(data, wcs, field, rows=7) == result


def test_other_band_flags_are_diagnostic_not_hard_exclusions():
    data, wcs, field = _fixture()
    result = _run(data, wcs, field, band="g")
    sq = result["scope_counts"]["square"]
    assert sq["instrumental"]["satur"] == 0
    assert sq["instrumental"]["allmask"] == 0
    assert sq["instrumental"]["usable"] == 24
    assert sq["quality_categories"]["satur"] == 0
    assert sq["quality_categories"]["allmask"] == 0
    assert sq["quality_categories"]["bad_numeric"] == 1


def test_zero_denominators_are_null_not_misleading_percentages():
    data, wcs, _ = _fixture()
    tiny = SkyField(359.9995, 0.2, 0.01)  # square only central pixel
    report = _run(data, wcs, tiny)
    assert report["scope_counts"]["square"]["pixels"] == 1
    assert report["scope_counts"]["sky_annulus_in_square"]["pixels"] == 0
    assert report["scope_counts"]["sky_annulus_in_square"]["usable_fraction"] is None
    assert all(value is None for value in report["scope_counts"][
        "sky_annulus_in_square"]["quality_fractions"].values())


def test_refuse_wrong_grids_masks_and_radii():
    data, wcs, field = _fixture()
    broken = data.copy()
    broken["nexp"] = data["nexp"][:, :-1]
    with pytest.raises(ValueError, match="common|nonempty|grid|quartet"):
        _run(broken, wcs, field)
    with pytest.raises(ValueError, match="radii|target"):
        mask_census_from_arrays(
            **data, wcs=wcs, field=field, band="r",
            target_radius_arcsec=10., sky_inner_arcsec=5.)
    with pytest.raises(ValueError, match="MASKBITS"):
        _run({**data, "maskbits": data["maskbits"].astype("f4")}, wcs, field)


def test_square_counts_match_independent_existing_square_coverage_module():
    from lsbpipe.surveys.square_coverage import square_coverage_from_arrays
    data, wcs, field = _fixture()
    detailed = _run(data, wcs, field)["scope_counts"]["square"]
    original = square_coverage_from_arrays(
        data["image"], data["invvar"], data["nexp"], data["maskbits"],
        wcs, field=field, band="r", chunk_rows=2)
    assert original.pixel_centers_sampled == detailed["pixels"]
    assert original.instrumentally_usable_pixels == detailed[
        "instrumental"]["usable"]
    for name in ("SATUR_R", "ALLMASK_R", "MEDIUM", "GALAXY"):
        assert original.maskbit_counts[name] == detailed["maskbits"][name]


def test_nonfinite_native_wcs_fails_closed_instead_of_losing_sky_pixels():
    data, _, field = _fixture()
    class BrokenWCS:
        has_celestial = True
        pixel_n_dim = 2
        def all_pix2world(self, x, y, origin):
            return np.full(x.shape, np.nan), np.full(y.shape, np.nan)
    with pytest.raises(ValueError, match="Nonfinite WCS"):
        _run(data, BrokenWCS(), field)


def test_signed_16bit_maskbits_preserves_high_bit_exactly():
    data, wcs, field = _fixture()
    data["maskbits"] = np.zeros((7, 7), dtype="i2")
    data["maskbits"][3, 3] = np.int16(-32768)  # bit 15 ALLMASK_I
    report = _run(data, wcs, field, band="r")
    target = report["scope_counts"]["target_in_square"]
    assert target["maskbits"]["ALLMASK_I"] == 1
    assert target["instrumental"]["allmask"] == 0
    assert target["quality_categories"]["other_bit_not_excluded"] == 1
