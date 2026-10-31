"""Offline, numerically stable original-FITS viewer raster tests."""
import numpy as np
import pytest

from lsbpipe.gui.fits_raster import (
    rgb_for_layer, overlay_rgba, fits_pixel_from_scene,
)


def test_qt_top_left_to_fits_bottom_up_pixel_coordinates():
    assert fits_pixel_from_scene(1.6, 0.4, (5, 7)) == (1, 4)
    assert fits_pixel_from_scene(1.6, 4.9, (5, 7)) == (1, 0)
    assert fits_pixel_from_scene(6.9, 3.1, (5, 7)) == (6, 1)
    assert fits_pixel_from_scene(-0.1, 2, (5, 7)) is None
    assert fits_pixel_from_scene(0, 5.01, (5, 7)) is None
    assert fits_pixel_from_scene(7, 0, (5, 7)) is None


def test_nan_infinite_and_zero_inverse_variance_cannot_appear_valid():
    image = np.array([
        [np.nan, -5., -1., 0.],
        [0.1, 1., 5., np.inf]], dtype="f4")
    source = image.copy()
    standard = rgb_for_layer(image, "image", "r")
    faint = rgb_for_layer(image, "image", "r", contrast="faint")
    assert standard.shape == (2, 4, 3)
    assert standard.dtype == np.uint8
    assert np.all(standard[0, 0] == 0)
    assert np.all(standard[1, 3] == 0)
    assert standard[0, 1, 0] <= standard[1, 2, 0]
    assert not np.array_equal(standard, faint)
    np.testing.assert_array_equal(image, source)
    weights = np.array([[0, 1, 10], [np.nan, -5, 100]], dtype="f4")
    ivar = rgb_for_layer(weights, "invvar", "r")
    assert np.all(ivar[0, 0] == 0)
    assert np.all(ivar[1, 0] == 0)
    assert np.all(ivar[1, 1] == 0)
    assert ivar[0, 1, 2] < ivar[1, 2, 2]


def test_maskbits_categorical_priority_and_priority_overlay_alpha():
    bits = np.array([
        [0, 1 << 12, 1 << 11, 1 << 3],
        [1 << 6, (1 << 12) | (1 << 11),
         (1 << 12) | (1 << 3), (1 << 11) | (1 << 6)],
    ], dtype="i4")
    original = bits.copy()
    rgb = rgb_for_layer(bits, "maskbits", "r")
    assert rgb.dtype == np.uint8
    assert rgb.shape == (2, 4, 3)
    assert tuple(rgb[0, 1]) != tuple(rgb[0, 2])
    assert tuple(rgb[0, 3]) == tuple(rgb[1, 2])
    assert tuple(rgb[0, 3]) == tuple(rgb[1, 3])
    rgba = overlay_rgba(
        bits, "r", show_satur=True, show_medium=True, show_galaxy=True)
    assert tuple(rgba[0, 0]) == (0, 0, 0, 0)
    assert tuple(rgba[0, 1]) == (77, 159, 230, 115)
    assert tuple(rgba[0, 2]) == (249, 183, 63, 115)
    assert tuple(rgba[0, 3]) == (232, 71, 67, 175)
    assert tuple(rgba[1, 2]) == (232, 71, 67, 175)
    assert tuple(rgba[1, 1]) == (249, 183, 63, 115)
    off = overlay_rgba(
        bits, "r", show_satur=False, show_medium=False, show_galaxy=False)
    assert np.count_nonzero(off) == 0
    np.testing.assert_array_equal(bits, original)


def test_negative_signed_maskbits_and_band_specific_saturation():
    raw = np.array([[-32768, 1 << 3, 1 << 2]], dtype="i2")
    # High unsigned bit is a nonzero warning in the categorical view.
    rgb = rgb_for_layer(raw, "maskbits", "r")
    assert tuple(rgb[0, 0]) != tuple(rgb_for_layer(
        np.zeros_like(raw), "maskbits", "r")[0, 0])
    r = overlay_rgba(
        raw, "r", show_satur=True, show_medium=False, show_galaxy=False)
    g = overlay_rgba(
        raw, "g", show_satur=True, show_medium=False, show_galaxy=False)
    assert r[0, 1, 3] == 175
    assert r[0, 2, 3] == 0
    assert g[0, 1, 3] == 0
    assert g[0, 2, 3] == 175


def test_nexp_is_linear_and_zero_is_black_not_invalid_flux():
    nexp = np.array([[0., 1., 2.], [3., 4., 5.]], dtype="f4")
    colors = rgb_for_layer(nexp, "nexp", "r")
    assert colors[0, 0, 2] == 0
    assert colors[1, 2, 2] == 255
    assert np.all(np.diff(colors[:, :, 2].ravel().astype(int)) > 0)


def test_invalid_layers_or_contrast_rejected():
    with pytest.raises(ValueError):
        rgb_for_layer(np.zeros((3, 3)), "science-adopted", "r")
    with pytest.raises(ValueError):
        rgb_for_layer(np.zeros((3, 3)), "image", "r", contrast="incorrect")
    with pytest.raises(ValueError):
        rgb_for_layer(np.zeros((3, 3, 3)), "image", "r")



def test_invalid_checkerboard_is_distinct_from_valid_zero_flux():
    from lsbpipe.gui.fits_raster import invalid_pixel_rgba
    image = np.array([[0., np.nan, 1.], [np.inf, -5., 0.]], dtype="f4")
    original = image.copy()
    mask = invalid_pixel_rgba(image, "image")
    assert mask.shape == (2, 3, 4)
    assert mask.dtype == np.uint8
    assert mask[0, 0, 3] == 0  # Real zero IMAGE is valid.
    assert mask[0, 2, 3] == 0
    assert mask[1, 1, 3] == 0  # Negative flux is not invalid.
    assert mask[0, 1, 3] > 0
    assert mask[1, 0, 3] > 0
    assert tuple(mask[0, 1, :3]) == (225, 55, 190)
    np.testing.assert_array_equal(image, original)

    invvar = np.array([[0., 2.], [-0.3, np.nan]], dtype="f4")
    bad_weights = invalid_pixel_rgba(invvar, "invvar")
    assert bad_weights[0, 0, 3] > 0
    assert bad_weights[0, 1, 3] == 0
    assert bad_weights[1, 0, 3] > 0
    assert bad_weights[1, 1, 3] > 0

    nexp = np.array([[0, 2], [3, 0]], dtype="i2")
    bad_exp = invalid_pixel_rgba(nexp, "nexp")
    assert bad_exp[0, 0, 3] > 0
    assert bad_exp[1, 1, 3] > 0
    assert bad_exp[0, 1, 3] == 0
    # MASKBITS zero is a legitimate bitfield, not a no-data declaration.
    assert not np.any(invalid_pixel_rgba(nexp, "maskbits"))

    with pytest.raises(ValueError):
        invalid_pixel_rgba(nexp, "not_a_layer")
    with pytest.raises(ValueError):
        invalid_pixel_rgba(nexp.ravel(), "nexp")
