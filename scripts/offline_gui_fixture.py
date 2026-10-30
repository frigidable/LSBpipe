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

    def setZoomFactor(self, zoom):
        self._zoom = float(zoom)

    def url(self):
        return QUrl("about:blank")

    def load(self, _url):
        raise AssertionError("Offline screenshot tests must not load remote Viewer")


@contextmanager
def offline_legacy_viewer():
    # Prevent Chromium GPU initialization on headless Windows runners.
    # Scientific state, other Qt widgets and callbacks remain real.
    with patch.object(gui, "QWebEngineView", OfflineViewer), \
         patch.object(gui, "_DiagnosticWebPage", lambda parent: None), \
         patch.object(gui.SurveyExplorer, "show_viewer", autospec=True,
                      return_value=None):
        yield
