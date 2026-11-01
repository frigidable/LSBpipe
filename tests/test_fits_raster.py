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



def test_original_fits_loader_does_not_preallocate_four_full_rgb_copies(
        tmp_path, monkeypatch):
    pytest.importorskip("astropy")
    from astropy.wcs import WCS
    from lsbpipe.gui import fits_raster
    from lsbpipe.surveys.legacy import BrickHit, SkyField
    field = SkyField(40.096208, 1.22925, 5.)
    hit = BrickHit("dr9", "south", "0401p012",
                   40., 40.2, 1.1, 1.4, {"r":9})
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [50., 50.]
    wcs.wcs.cdelt = [-0.262/3600., 0.262/3600.]
    wcs.wcs.crval = [field.ra_deg,field.dec_deg]
    wcs.wcs.ctype = ["RA---TAN","DEC--TAN"]
    layers = {
        "image":np.full((100,100), 1.5, dtype="f4"),
        "invvar":np.full((100,100), 4., dtype="f4"),
        "nexp":np.full((100,100), 7, dtype="i2"),
        "maskbits":np.zeros((100,100), dtype="i4"),
    }
    monkeypatch.setattr(
        fits_raster, "science_data_root",
        lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        fits_raster, "_load_four_cutouts",
        lambda *a, **k: (layers,wcs,{k:1 for k in layers}))
    monkeypatch.setattr(
        fits_raster, "rgb_for_layer",
        lambda *a, **k: pytest.fail("Eager RGB copies are forbidden"))
    result = fits_raster.prepare_fits_explorer(
        directory=tmp_path, field=field, hit=hit, band="r")
    assert "previews" not in result
    assert result["data"]["image"] is layers["image"]
    assert all(not array.flags.writeable for array in layers.values())
    assert result["hdu_indices"] == {k:1 for k in layers}
    assert set(result["outlines"]) == {
        "square","target","sky_inner","sky_outer"}
