"""Offline GUI integration contracts for the hosted Legacy Sky Viewer.

These tests verify URL navigation and failure paths, not live page rendering,
which requires on-device network/QtWebEngine interaction.
"""
import inspect
import pytest

pytest.importorskip("PySide6")
from lsbpipe.gui.survey_explorer import SurveyExplorer, _DiagnosticWebPage


def test_viewer_is_top_level_not_aladin_or_iframe():
    ui = inspect.getsource(SurveyExplorer._build_ui)
    method = inspect.getsource(SurveyExplorer.show_viewer)
    assert "QWebEngineView()" in ui
    assert "QWebEnginePage" not in method
    assert "self.sky.load(QUrl(link))" in method
    assert "self._viewer_link()" in method
    assert "setHtml" not in method
    assert "Aladin" not in ui
    assert "_serve_aladin_page" not in ui


def test_coords_need_explicit_navigation_to_avoid_network_storms():
    ui = inspect.getsource(SurveyExplorer._build_ui)
    assert "self.center_btn.clicked.connect(self.show_viewer)" in ui
    assert "self.ra.valueChanged.connect" not in ui
    assert "self.dec.valueChanged.connect" not in ui
    assert "self.width.valueChanged.connect" not in ui


def test_browser_fallback_and_diagnostics():
    assert "QDesktopServices.openUrl(QUrl(self._viewer_link()))" in (
        inspect.getsource(SurveyExplorer.open_viewer_in_browser))
    diag = inspect.getsource(SurveyExplorer.show_viewer_diagnostics)
    assert "JSON.stringify" in diag
    assert "recent_console" in diag
    assert "documentReady" in diag
    assert hasattr(_DiagnosticWebPage, "javaScriptConsoleMessage")


def test_release_region_picker_is_conservative():
    code = inspect.getsource(SurveyExplorer._preferred_viewer_region)
    assert "32.375" in code
    assert "return None" in code
    assert "release in ('dr9', 'dr11')" in code
