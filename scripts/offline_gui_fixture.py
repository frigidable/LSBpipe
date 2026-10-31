"""Offline Qt widget substitute for the hosted Legacy Viewer in CI.

This replaces QWebEngineView only while an explicit test context is active.
It is not a substitute for browser rendering or on-device validation.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QWidget

from lsbpipe.gui import survey_explorer as gui


class OfflineViewer(QWidget):
    loadStarted = Signal()
    loadFinished = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0

    def setPage(self, _page):
        pass

    def page(self):
        return self

    def runJavaScript(self, script, callback=None):
        # Never execute remote JS in offline tests; honor failure callback.
        self._last_script = script
        if callback is not None:
            callback("unavailable")

    def setZoomFactor(self, zoom):
        self._zoom = float(zoom)

    def url(self):
        return QUrl("about:blank")

    def load(self, url):
        # Record navigation requests without opening sockets or creating
        # Chromium. Brick-selection GUI tests must exercise real signals.
        if not isinstance(url, QUrl):
            raise TypeError("Expected QUrl")
        if url.scheme() != "https" or url.host() != "www.legacysurvey.org":
            raise AssertionError("Unexpected viewer host in offline fixture")
        self._requested_url = QUrl(url)

    def last_requested_url(self):
        return getattr(self, "_requested_url", QUrl())


@contextmanager
def offline_legacy_viewer():
    # Prevent Chromium GPU initialization on headless Windows runners.
    # Scientific state, other Qt widgets and callbacks remain real.
    with patch.object(gui, "QWebEngineView", OfflineViewer), \
         patch.object(gui, "_DiagnosticWebPage", lambda parent: None), \
         patch.object(gui.SurveyExplorer, "show_viewer", autospec=True,
                      return_value=None):
        yield


def make_offline_catalog():
    """Eight release records, four unique north bricks, 88 URL candidates.

    All numbers are synthetic. No network request and no actual sky coverage
    or source FITS are represented by this dataset.
    """
    from lsbpipe.surveys.legacy import BrickHit, SkyField, build_manifest
    field = SkyField(154.7709, 46.4537, 5.)
    names = ("1544p465", "1546p462", "1548p465", "1549p462")
    hits = []
    for release in ("dr9", "dr11"):
        for name in names:
            hits.append(BrickHit(
                release, "north", name, 154.2, 155.0, 46.1, 46.8,
                {"g": 3, "r": 3, "z": 3}))
    return hits, build_manifest(field, hits)


def apply_offline_state(window, state: str):
    """Populate real Qt widgets with deterministic fake catalog/file states."""
    from unittest.mock import patch
    from lsbpipe.gui import survey_explorer as gui
    if state == "S0":
        return
    hits, manifest = make_offline_catalog()
    window._receive_search((hits, manifest))
    if state == "S1":
        return
    if state == "S2":
        window.band_filter.setCurrentText("r")
        window.kind_filter.setCurrentText("image")
        assert len(window._selected()) == 88  # filters never discard hidden selection
        return
    if state in ("S3", "S4", "S5", "S6"):
        window.bricks.selectRow(0)
        with patch.object(gui.QInputDialog, "getItem", return_value=("r", True)):
            window.select_science_quartet()
        assert len(window._selected()) == 4
        window.brick_filter.setCurrentText(hits[0].name)
        if state in ("S4", "S5", "S6"):
            # Visual-only synthetic state: never claim original FITS loaded.
            window.science_qa.directory.setText(
                "C:/synthetic-placeholder/NOT_REAL_FITS")
            window.science_qa.result.setPlainText(
                "OFFLINE SYNTHETIC QA STATE — NO FITS OPENED\n"
                "Selected DR9 north 1544p465, r band.\n"
                "Use Check full search square, Locate mask footprints, "
                "or Open four-layer FITS map with downloaded originals.")
            window.inspector_tabs.setCurrentWidget(window.science_qa)
            if state == "S5":
                window.fits_explorer.set_dataset(make_synthetic_fits_view())
                window.preview_tabs.setCurrentWidget(window.fits_explorer)
            elif state == "S6":
                window.science_qa.steps.setCurrentIndex(2)
                window.science_qa.north_directory.setText(
                    "C:/synthetic/NORTH-NOT-REAL")
                window.science_qa.south_directory.setText(
                    "C:/synthetic/SOUTH-NOT-REAL")
                window.science_qa.result.setPlainText(
                    "SYNTHETIC COMPARE PANEL — NO ORIGINAL FITS READ\n"
                    "Original north and south roots are required.\n"
                    "Strict native WCS check; no stacking.")
        return
    raise ValueError(f"Unknown offline state: {state}")



def make_synthetic_fits_view():
    """Explicitly fake pixels for S5 GUI screen-layout evidence, not science."""
    import numpy as np
    from astropy.wcs import WCS
    from lsbpipe.gui.fits_raster import rgb_for_layer
    from lsbpipe.surveys.legacy import SkyField
    field = SkyField(154.7709, 46.4537, 5.)
    shape = (320, 320)
    y, x = np.mgrid[:320, :320]
    gaussian = np.exp(-((x-160.)**2/2300 + (y-162.)**2/1300))
    companion = np.exp(-((x-225.)**2+(y-100.)**2)/110.)
    noise = np.random.default_rng(104).normal(0, 0.22, shape)
    image = (gaussian*6. + companion*17. + noise).astype("f4")
    maskbits = np.zeros(shape, dtype=np.int32)
    maskbits[(x-160.)**2/280. + (y-162.)**2/180. < 1] |= (1 << 12)
    maskbits[((x-225.)**2+(y-100.)**2) < 25**2] |= (1 << 11)
    maskbits[((x-225.)**2+(y-100.)**2) < 7**2] |= (1 << 3)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [160.,160.]
    wcs.wcs.cdelt = [-1./3600,1./3600]
    wcs.wcs.crval = [field.ra_deg,field.dec_deg]
    wcs.wcs.ctype = ["RA---TAN","DEC--TAN"]
    data = {
        "image":image,
        "invvar":np.full(shape,4.,dtype="f4"),
        "nexp":np.full(shape,8,dtype="i2"),
        "maskbits":maskbits,
    }
    return {
        "status":"DIAGNOSTIC_NOT_ADOPTED",
        "data":data,"wcs":wcs,
        "previews":{k:rgb_for_layer(v,k,"r") for k,v in data.items()},
        "outlines":{
            "square":((35,35),(285,35),(285,285),(35,285),(35,35)),
            "target":tuple((160+35*np.cos(t),160+35*np.sin(t))
                for t in np.linspace(0,2*np.pi,128)),
            "sky_inner":tuple((160+65*np.cos(t),160+65*np.sin(t))
                for t in np.linspace(0,2*np.pi,128)),
            "sky_outer":tuple((160+110*np.cos(t),160+110*np.sin(t))
                for t in np.linspace(0,2*np.pi,128)),
        },
        "hdu_indices":{"image":1,"invvar":1,"nexp":1,"maskbits":1},
        "field":field,"band":"r","pixel_shape":shape,
        "source":"SYNTHETIC FIXTURE · no original FITS opened",
        "data_root":"OFFLINE_SYNTHETIC_NOT_REAL",
    }
