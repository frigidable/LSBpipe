"""Spatial component census on native MASKBITS: WCS + square membership."""
import numpy as np
import pytest

pytest.importorskip("astropy")
from astropy.wcs import WCS
from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.mask_localization import mask_components_from_array


def synthetic_masks():
    w = WCS(naxis=2)
    w.wcs.crpix = [120.5, 120.5]
    w.wcs.cdelt = [-1 / 3600., 1 / 3600.]
    w.wcs.crval = [40.096208, 1.229250]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    bits = np.zeros((240, 240), dtype=np.int32)
    bits[116:119, 116:119] = 1 << 3  # 9 saturated target px
    bits[100:105, 102:106] = 1 << 12  # SGA near central target
    bits[166:170, 166:170] = 1 << 11  # 16 MEDIUM near 65-110" annulus
    bits[193:198, 193:198] = 1 << 11  # outside 2' TAN square
    return w, bits


def test_position_aware_component_counts_and_intersections():
    w, bits = synthetic_masks()
    target = SkyField(40.096208, 1.229250, 2.)
    report = mask_components_from_array(
        bits, w, field=target,
        target_radius_arcsec=35., sky_inner_arcsec=65.,
        sky_outer_arcsec=110.)
    assert report["status"] == "DIAGNOSTIC_NOT_ADOPTED"
    assert report["connectivity"].startswith("8-neighbor")
    sat = report["bits"]["SATUR_R"]
    sga = report["bits"]["GALAXY"]
    medium = report["bits"]["MEDIUM"]
    assert sat["total_pixels_inside_square"] == 9
    assert sat["connected_regions"] == 1
    assert sat["total_target_circle_pixels"] == 9
    assert sat["total_sky_annulus_pixels"] == 0
    assert sat["regions"][0]["pixels"] == 9
    assert sat["regions"][0]["angular_separation_arcsec"] < 10.
    assert sga["total_pixels_inside_square"] == 20
    assert sga["connected_regions"] == 1
    assert sga["total_target_circle_pixels"] == 20
    assert medium["total_pixels_inside_square"] == 16
    assert medium["connected_regions"] == 1
    assert medium["total_sky_annulus_pixels"] > 0
    assert medium["regions"][0]["centroid_ra_deg"] != target.ra_deg
    assert medium["regions"][0]["centroid_dec_deg"] != target.dec_deg
    assert medium["regions"][0]["touches_square_boundary"] is False
    assert report["bits"]["BRIGHT"]["total_pixels_inside_square"] == 0
    assert report["bits"]["ALLMASK_R"]["connected_regions"] == 0


def test_components_near_ra_wrap_and_signed16_high_bit():
    w, bits = synthetic_masks()
    w.wcs.crval[0] = 359.999
    bits[100, 100] |= 1 << 12
    target = SkyField(359.999, 1.22925, 2.)
    report = mask_components_from_array(bits, w, field=target)
    assert report["bits"]["GALAXY"]["total_pixels_inside_square"] == 20
    assert 0 <= report["bits"]["GALAXY"]["regions"][0][
        "centroid_ra_deg"] < 360


def test_multiple_isolated_patches_are_sorted_by_size():
    w, bits = synthetic_masks()
    bits[140:142, 140:142] |= 1 << 3
    bits[140:142, 150:152] |= 1 << 3
    target = SkyField(40.096208, 1.22925, 2.)
    report = mask_components_from_array(bits, w, field=target)
    saturated = report["bits"]["SATUR_R"]
    assert saturated["total_pixels_inside_square"] == 17
    assert saturated["connected_regions"] == 3
    assert [x["pixels"] for x in saturated["regions"]] == [9, 4, 4]


def test_invalid_radii_and_band_fail_closed():
    w, bits = synthetic_masks()
    target = SkyField(40.096208, 1.22925, 2.)
    with pytest.raises(ValueError, match="radii"):
        mask_components_from_array(bits, w, field=target,
                                   sky_inner_arcsec=12, sky_outer_arcsec=8)
    with pytest.raises(ValueError, match="Invalid band"):
        mask_components_from_array(bits, w, field=target, band="u")
    with pytest.raises(ValueError, match="2D"):
        mask_components_from_array(bits.ravel(), w, field=target)


def test_original_mask_immutable_and_component_report_is_json_safe():
    import json
    w, bits = synthetic_masks()
    saved = bits.copy()
    report = mask_components_from_array(
        bits, w, field=SkyField(40.096208, 1.229250, 2.))
    np.testing.assert_array_equal(bits, saved)
    json.dumps(report, allow_nan=False)
