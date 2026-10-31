"""Numeric FITS transfer and conservative five-class policy tests."""
import numpy as np
import pytest
from lsbpipe.gui.display_transfer import (
    display_range, display_gray, transfer_settings
)
from lsbpipe.gui.fits_raster import rgb_for_layer, overlay_rgba
from lsbpipe.gui.quality_map import quality_classes, quality_rgb, quality_summary


def test_manual_image_ranges_are_native_units_and_reversible():
    raw = np.array([[-5., 0., 5., 10.]], dtype=np.float32)
    expected = raw.copy()
    linear = display_gray(
        raw, "image", scaling="linear", limits=(-5., 10.))
    assert list(linear[0]) == [0, 85, 170, 255]
    asinh = display_gray(
        raw, "image", scaling="asinh", limits=(-5., 10.))
    assert asinh[0,0] == 0 and asinh[0,-1] == 255
    assert asinh[0,2] > linear[0,2]
    rgb = rgb_for_layer(
        raw, "image", "r", scaling="linear", limits=(-5., 10.))
    assert rgb[0,1,0] == linear[0,1]
    np.testing.assert_array_equal(raw, expected)


def test_log_transfer_rejects_nonpositive_bounds_and_masks_negative_flux():
    img = np.array([[-10., 0., 1., 10., 100.]], dtype=np.float32)
    out = display_gray(img, "image", scaling="log", limits=(1., 100.))
    assert out[0,0] == 0 and out[0,1] == 0
    assert out[0,2] == 0
    assert 120 <= out[0,3] <= 135
    assert out[0,4] == 255
    with pytest.raises(ValueError, match="vmin > 0"):
        display_gray(img, "image", scaling="log", limits=(-1., 100.))
    with pytest.raises(ValueError, match="vmin > 0"):
        display_range(img, "invvar", scaling="log", limits=(0., 100.))


def test_inverse_variance_and_nexp_are_not_flux_stretched():
    ivar = np.array([[0., 1., 10., 100.]], dtype=np.float32)
    result = display_gray(
        ivar, "invvar", scaling="log", limits=(1., 100.))
    assert list(result[0]) == [0, 0, 128, 255]
    nexp = np.array([[0, 1, 2, 4]], dtype=np.int16)
    assert list(display_gray(
        nexp, "nexp", scaling="linear", limits=(0., 4.))[0]) == [
            0, 64, 128, 255]
    with pytest.raises(ValueError, match="Unsupported"):
        display_gray(nexp, "nexp", scaling="asinh")
    with pytest.raises(ValueError, match="must not be negative"):
        display_gray(nexp, "nexp", limits=(-1., 4.))


def test_invalid_manual_bounds_and_zero_valid_samples():
    src = np.array([[np.nan, np.inf, -np.inf]], dtype=np.float64)
    auto = display_gray(src, "image")
    assert np.all(auto == 0)
    for limits in ((3., 3.), (5., 2.), (np.nan, 6.), (0., np.inf)):
        with pytest.raises(ValueError, match="limits"):
            display_gray(src, "image", limits=limits)
    assert transfer_settings("image") == ("asinh", None)


def test_quality_categories_match_current_conservative_instrumental_policy():
    bits = np.array([[
        0, (1 << 12), (1 << 11), (1 << 1),
        (1 << 3), (1 << 6), (1 << 3) | (1 << 6), 0
    ]], dtype=np.int32)
    data = {
        "image": np.zeros(bits.shape, dtype=np.float32),
        "invvar": np.ones(bits.shape, dtype=np.float32),
        "nexp": np.ones(bits.shape, dtype=np.int16),
        "maskbits": bits,
    }
    data["invvar"][0,7] = 0.
    expected = [0,1,1,1,2,3,3,4]
    np.testing.assert_array_equal(quality_classes(data,"r")[0], expected)
    report = quality_summary(data, "r")
    assert report["status"] == "DIAGNOSTIC_NOT_ADOPTED"
    assert report["categories"] == {
        "clean":1,"catalog_warning_not_excluded":3,
        "satur":1,"allmask":2,"bad_numeric":1}
    assert quality_rgb(data,"r").shape == (1,8,3)
    # g-band saturation does NOT exclude r-band pixels.
    assert quality_classes(data,"g")[0,4] == 0
    assert quality_classes(data,"g")[0,5] == 0


def test_independent_bit_overlays_with_priority_and_no_fits_mutation():
    bits = np.array([[
        0, 1 << 3, 1 << 6, (1 << 3) | (1 << 6),
        1 << 1, 1 << 12
    ]], dtype="i4")
    saved = bits.copy()
    common = dict(show_medium=False, show_galaxy=True, show_invalid=False)
    satur = overlay_rgba(
        bits, "r", show_satur=True, show_allmask=False,
        show_bright=False, **common)
    assert satur[0,1,3] == 175
    assert satur[0,2,3] == 0
    assert satur[0,3,3] == 175
    allmask = overlay_rgba(
        bits, "r", show_satur=False, show_allmask=True,
        show_bright=False, **common)
    assert allmask[0,1,3] == 0
    assert allmask[0,2,3] == 175
    bright = overlay_rgba(
        bits, "r", show_satur=False, show_allmask=False,
        show_bright=True, **common)
    assert bright[0,4,3] > 0
    assert bright[0,5,3] > 0  # GALAXY warning is retained
    np.testing.assert_array_equal(bits, saved)
