"""Real PySide6, offline interactive-FITS GUI behavior checks."""
import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")
pytest.importorskip("astropy")

from astropy.wcs import WCS
from PySide6.QtWidgets import QApplication

from lsbpipe.gui.fits_explorer import FITSExplorer
from lsbpipe.surveys.legacy import SkyField


def synthetic_dataset():
    field = SkyField(40.096208, 1.22925, 5.)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [4., 3.]
    wcs.wcs.cdelt = [-0.262 / 3600., 0.262 / 3600.]
    wcs.wcs.crval = [field.ra_deg, field.dec_deg]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    image = np.arange(48, dtype="f4").reshape(6, 8)
    image[0, 0] = 999.
    maskbits = np.zeros((6, 8), dtype="i4")
    maskbits[5, 2] = 1 << 12
    maskbits[0, 1] = 1 << 3
    data = {
        "image":image,
        "invvar":np.ones((6,8), dtype="f4"),
        "nexp":np.full((6,8), 9, dtype="i2"),
        "maskbits":maskbits,
    }
    from lsbpipe.gui.fits_raster import rgb_for_layer
    previews = {
        name:rgb_for_layer(arr, name, "r") for name,arr in data.items()
    }
    # WCS-sampled paths would be more complex; here the y-inversion
    # and the absolute scene mapping are tested independently.
    outlines = {
        "square":((1.,1.),(5.,1.),(5.,4.),(1.,4.),(1.,1.)),
        "target":((2.,2.),(3.,2.),(3.,3.),(2.,2.)),
        "sky_inner":((1.,1.),(6.,1.),(6.,4.),(1.,1.)),
        "sky_outer":((0.,0.),(7.,0.),(7.,5.),(0.,0.)),
    }
    return {
        "status":"DIAGNOSTIC_NOT_ADOPTED",
        "data":data, "previews":previews, "wcs":wcs, "outlines":outlines,
        "hdu_indices":{"image":1,"invvar":1,"nexp":1,"maskbits":1},
        "field":field, "band":"r",
        "source":"DR9 south · 0401p012 · r",
        "pixel_shape":(6,8), "data_root":"C:/synthetic/not-real",
    }


@pytest.fixture
def explorer(qtbot):
    widget = FITSExplorer()
    qtbot.addWidget(widget)
    widget.resize(940,700)
    widget.show()
    return widget


def test_layer_switch_and_native_wcs_pixel_readout(explorer, qtbot):
    data = synthetic_dataset()
    explorer.set_dataset(data)
    assert explorer.layer.currentData() == "image"
    assert explorer.scene.sceneRect().width() == 8
    assert explorer.scene.sceneRect().height() == 6
    assert "original HDU" in explorer.context.text()
    explorer.layer.setCurrentIndex(2)
    assert explorer.layer.currentData() == "nexp"
    explorer.inspect_pixel(2.1,0.2)  # top of Qt canvas is FITS y=5
    line = explorer.pixel_info.text()
    assert "Pixel (2, 5)" in line
    assert "NEXP 9" in line
    assert "GALAXY" in line
    explorer.layer.setCurrentIndex(3)
    assert explorer.layer.currentData() == "maskbits"
    explorer.inspect_pixel(1.2,5.2)  # bottom is FITS y=0
    assert "Pixel (1, 0)" in explorer.pixel_info.text()
    assert "SATUR_R" in explorer.pixel_info.text()
    explorer.inspect_pixel(9,0)
    assert "Outside" in explorer.pixel_info.text()
    assert data["data"]["image"][0,0] == 999.


def test_screen_image_is_vertically_flipped_in_qimage(explorer):
    data = synthetic_dataset()
    explorer.set_dataset(data)
    screen = explorer.base_item.pixmap().toImage()
    rgb = data["previews"]["image"]
    assert screen.pixelColor(0,0).red() == int(rgb[5,0,0])
    assert screen.pixelColor(0,5).red() == int(rgb[0,0,0])


def test_overlays_are_toggleable_without_changing_science_pixels(explorer):
    dataset = synthetic_dataset()
    original = dataset["data"]["maskbits"].copy()
    explorer.set_dataset(dataset)
    assert explorer.flags["SATUR"].isChecked()
    assert explorer.flags["MEDIUM"].isChecked()
    assert not explorer.flags["GALAXY"].isChecked()
    explorer.flags["GALAXY"].setChecked(True)
    # FITS y=5 corresponds to uppermost screen row.
    alpha = explorer.overlay_item.pixmap().toImage()
    assert alpha.pixelColor(2,0).alpha() > 0
    assert alpha.pixelColor(1,5).alpha() > 0
    explorer.flags["SATUR"].setChecked(False)
    explorer.flags["GALAXY"].setChecked(False)
    alpha_off = explorer.overlay_item.pixmap().toImage()
    assert alpha_off.pixelColor(2,0).alpha() == 0
    assert alpha_off.pixelColor(1,5).alpha() == 0
    np.testing.assert_array_equal(dataset["data"]["maskbits"], original)


def test_reset_drops_previous_raster_and_provenance(explorer):
    explorer.set_dataset(synthetic_dataset())
    assert not explorer.base_item.pixmap().isNull()
    explorer.clear_data()
    assert explorer._dataset is None
    assert explorer.base_item.pixmap().isNull()
    assert explorer.overlay_item.pixmap().isNull()
    assert "Rerun" not in explorer.pixel_info.text() or "Reload" in explorer.pixel_info.text()
    assert "No local science" in explorer.context.text()


def test_rejects_non_diagnostic_or_unregistered_science(explorer):
    report = synthetic_dataset()
    report["status"] = "SCIENCE_ADOPTED"
    with pytest.raises(ValueError, match="diagnostic-only"):
        explorer.set_dataset(report)
    report = synthetic_dataset()
    report["data"]["nexp"] = np.ones((2,2))
    with pytest.raises(ValueError, match="share a 2D pixel grid"):
        explorer.set_dataset(report)



def test_no_data_visually_distinct_from_true_zero_and_toggleable(explorer):
    dataset = synthetic_dataset()
    dataset["data"]["image"][2, 3] = np.nan
    dataset["data"]["image"][1, 1] = 0.  # finite zero is valid
    dataset["data"]["invvar"][3, 4] = 0.
    dataset["data"]["nexp"][4, 5] = 0
    explorer.set_dataset(dataset)
    assert explorer.flags["INVALID"].isChecked()

    # The composite overlay marks IMAGE NaN, zero INVVAR and zero NEXP,
    # regardless of selected display layer; FITS y-axis runs upward.
    image = explorer.overlay_item.pixmap().toImage()
    assert image.pixelColor(3, 3).alpha() > 0
    assert image.pixelColor(4, 2).alpha() > 0
    assert image.pixelColor(5, 1).alpha() > 0
    assert image.pixelColor(1, 4).alpha() == 0

    explorer.layer.setCurrentIndex(1)
    same_mask = explorer.overlay_item.pixmap().toImage()
    assert same_mask.pixelColor(4, 2).alpha() > 0

    explorer.flags["INVALID"].setChecked(False)
    hidden = explorer.overlay_item.pixmap().toImage()
    assert hidden.pixelColor(3, 3).alpha() == 0
    assert hidden.pixelColor(4, 2).alpha() == 0
    assert hidden.pixelColor(5, 1).alpha() == 0
    assert dataset["data"]["invvar"][3, 4] == 0.
    assert dataset["data"]["nexp"][4, 5] == 0



def test_large_local_fits_renders_off_ui_thread_and_drops_stale_requests(
        explorer, qtbot):
    from lsbpipe.gui.fits_raster import rgb_for_layer
    dataset = synthetic_dataset()
    size = 560
    dataset["data"] = {
        kind: np.resize(values, (size, size)).copy()
        for kind, values in dataset["data"].items()
    }
    dataset["previews"] = {}  # real originals now render on demand
    dataset["pixel_shape"] = (size, size)
    explorer.set_dataset(dataset)
    assert explorer._render_future is not None
    # Rapid edits may coalesce, but the *last* layer/contrast must win.
    explorer.layer.setCurrentIndex(1)
    explorer.contrast.setCurrentIndex(1)
    explorer.layer.setCurrentIndex(2)
    explorer.contrast.setCurrentIndex(0)
    qtbot.waitUntil(
        lambda: explorer._render_future is None and
                explorer._render_requested is None and
                explorer._displayed_base_key == ("base", "nexp", "standard", "auto", None),
        timeout=20000)
    image = explorer.base_item.pixmap().toImage()
    expected = rgb_for_layer(dataset["data"]["nexp"], "nexp", "r")
    assert image.width() == size
    assert image.height() == size
    assert image.pixelColor(22, 1).blue() == int(expected[size-2, 22, 2])
    assert explorer._raster_cache.bytes_used <= 48 * 1024 * 1024
    assert explorer._raster_cache.count <= 4
    assert "original pixels" in explorer.render_status.text().lower()


def test_render_from_old_fits_never_reappears_after_clearing(
        explorer, qtbot):
    dataset = synthetic_dataset()
    size = 550
    dataset["data"] = {
        kind: np.resize(values, (size, size)).copy()
        for kind, values in dataset["data"].items()
    }
    dataset["previews"] = {}
    dataset["pixel_shape"] = (size, size)
    explorer.set_dataset(dataset)
    assert explorer._render_future is not None
    explorer.clear_data()
    assert explorer.base_item.pixmap().isNull()
    qtbot.waitUntil(
        lambda: explorer._render_future is None, timeout=20000)
    assert explorer._dataset is None
    assert explorer.base_item.pixmap().isNull()
    assert explorer._raster_cache.count == 0


def test_full_frame_viewer_refuses_unbounded_memory_without_silent_downsample(
        explorer):
    report = synthetic_dataset()
    huge = np.broadcast_to(
        np.zeros((1, 1), dtype=np.float32), (2500, 2500))
    report["data"] = {
        "image": huge, "invvar": huge,
        "nexp": huge, "maskbits": huge.astype(np.int32)}
    report["pixel_shape"] = (2500, 2500)
    with pytest.raises(ValueError, match="6 million-pixel"):
        explorer.set_dataset(report)



def test_native_unit_ranges_and_scales_never_modify_original_pixels(explorer):
    dataset = synthetic_dataset()
    source = dataset["data"]["image"].copy()
    explorer.set_dataset(dataset)
    assert explorer.range_info.text().startswith("IMAGE")
    explorer.vmin.setText("-2.5")
    explorer.vmax.setText("12")
    explorer.apply_limits.click()
    assert explorer._manual_ranges["image"] == (-2.5, 12.)
    assert "nanomaggy" in explorer.range_info.text()
    assert "-2.5" in explorer.range_info.text()
    assert "12" in explorer.range_info.text()
    explorer.scaling.setCurrentIndex(explorer.scaling.findData("linear"))
    assert explorer._displayed_base_key[3] == "linear"
    explorer.vmin.setText("inf")
    explorer.vmax.setText("4")
    explorer.apply_limits.click()
    assert "rejected" in explorer.range_info.text()
    assert explorer._manual_ranges["image"] == (-2.5, 12.)
    explorer.auto_limits.click()
    assert "image" not in explorer._manual_ranges
    assert explorer.vmin.text() == ""
    assert explorer.vmax.text() == ""
    np.testing.assert_array_equal(dataset["data"]["image"], source)


def test_split_satur_allmask_bright_and_quality_map(explorer):
    dataset = synthetic_dataset()
    bits = dataset["data"]["maskbits"]
    bits[1, 2] = 1 << 6
    bits[2, 2] = 1 << 1
    bits[3, 2] = 1 << 12
    explorer.set_dataset(dataset)
    explorer.flags["BRIGHT"].setChecked(True)
    explorer.flags["SATUR"].setChecked(False)
    alpha = explorer.overlay_item.pixmap().toImage()
    # Qt screen Y is reversed relative to original FITS rows.
    assert alpha.pixelColor(1,5).alpha() == 0  # SATUR_R alone
    assert alpha.pixelColor(2,4).alpha() > 0   # ALLMASK_R
    assert alpha.pixelColor(2,3).alpha() > 0   # BRIGHT
    explorer.flags["ALLMASK"].setChecked(False)
    alpha = explorer.overlay_item.pixmap().toImage()
    assert alpha.pixelColor(2,4).alpha() == 0
    explorer.layer.setCurrentIndex(explorer.layer.findData("quality"))
    assert explorer.layer.currentData() == "quality"
    assert explorer.range_info.text().startswith("QUALITY")
    assert not explorer.vmin.isEnabled()
    colored = explorer.base_item.pixmap().toImage()
    # SGA target is an instrumentally valid warning (amber), not excluded.
    assert colored.pixelColor(2,0).red() == 228
    assert colored.pixelColor(2,0).green() == 179
    assert colored.pixelColor(1,5).red() == 220   # SATUR_R class
    assert colored.pixelColor(2,4).red() == 239   # ALLMASK_R class
    assert colored.pixelColor(2,3).red() == 228   # BRIGHT warning
    assert explorer.overlay_item.pixmap().toImage().pixelColor(2,3).alpha() == 0


def test_empty_positive_log_layer_hides_colorbar_without_lying(explorer):
    data = synthetic_dataset()
    data["data"]["invvar"][:] = 0.
    data["data"]["image"][:] = -10.
    data["previews"] = {}  # Ensure actual renderer runs, not an old preview.
    explorer.set_dataset(data)
    explorer.layer.setCurrentIndex(explorer.layer.findData("invvar"))
    assert "Display rendering failed" not in explorer.render_status.text()
    assert "log unavailable" in explorer.range_info.text()
    assert (explorer.scale_bar.pixmap() is None or
            explorer.scale_bar.pixmap().isNull())
    explorer.layer.setCurrentIndex(explorer.layer.findData("image"))
    explorer.scaling.setCurrentIndex(explorer.scaling.findData("log"))
    assert "Display rendering failed" not in explorer.render_status.text()
    assert "log unavailable" in explorer.range_info.text()
    assert (explorer.scale_bar.pixmap() is None or
            explorer.scale_bar.pixmap().isNull())


def test_blank_manual_display_limits_are_auto_not_float_conversion_error(
        explorer):
    # The real Windows FITS view initially has empty vmin/vmax placeholders.
    explorer.vmin.setText("")
    explorer.vmax.setText("")
    explorer._apply_display_range()
    assert "could not convert string" not in explorer.range_info.text()
    assert "image" not in explorer._manual_ranges
    explorer.vmin.setText("1.5")
    explorer._apply_display_range()
    assert "Enter both numeric" in explorer.range_info.text()
    assert "image" not in explorer._manual_ranges
