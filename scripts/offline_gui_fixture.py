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
    field = SkyField(154.7709, 46.4537, 15.)
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
    if state == "S3":
        window.bricks.selectRow(0)
        with patch.object(gui.QInputDialog, "getItem", return_value=("r", True)):
            window.select_science_quartet()
        assert len(window._selected()) == 4
        window.brick_filter.setCurrentText(hits[0].name)
        return
    raise ValueError(f"Unknown offline state: {state}")
