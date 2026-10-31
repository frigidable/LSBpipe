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



def test_no_data_visually_distinct_from_valid_zero_and_toggleable(explorer):
    dataset = synthetic_dataset()
    dataset["data"]["image"][2, 3] = np.nan
    dataset["data"]["image"][1, 1] = 0.  # real finite zero flux
    dataset["data"]["invvar"][3, 4] = 0.
    dataset["data"]["nexp"][4, 5] = 0
    explorer.set_dataset(dataset)
    assert explorer.flags["INVALID"].isChecked()

    # Qt display row = ny - 1 - FITS row.
    img = explorer.invalid_item.pixmap().toImage()
    assert img.pixelColor(3, 3).alpha() > 0
    assert img.pixelColor(1, 4).alpha() == 0

    explorer.layer.setCurrentIndex(1)  # INVVAR
    iv = explorer.invalid_item.pixmap().toImage()
    assert iv.pixelColor(4, 2).alpha() > 0

    explorer.layer.setCurrentIndex(2)  # NEXP
    exp = explorer.invalid_item.pixmap().toImage()
    assert exp.pixelColor(5, 1).alpha() > 0

    explorer.flags["INVALID"].setChecked(False)
    hidden = explorer.invalid_item.pixmap().toImage()
    assert hidden.pixelColor(5, 1).alpha() == 0
    assert dataset["data"]["invvar"][3, 4] == 0.
    assert dataset["data"]["nexp"][4, 5] == 0
