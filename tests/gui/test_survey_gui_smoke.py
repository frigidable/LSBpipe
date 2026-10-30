"""Offline Survey Explorer behavior smoke tests; no archive or Viewer I/O."""
from __future__ import annotations

from scripts.offline_gui_fixture import offline_legacy_viewer

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from lsbpipe.gui.survey_explorer import SurveyExplorer


@pytest.fixture
def offline_explorer(qtbot):
    # Patch the only auto navigation path, before building the window.
    # The real Qt widgets and signals remain active in this test.
    with offline_legacy_viewer():
        window = SurveyExplorer()
    qtbot.addWidget(window)
    window.show()
    yield window


def test_startup_is_experimental_and_offline(offline_explorer):
    window = offline_explorer
    assert "experimental" in window.windowTitle().lower()
    assert "No catalogs queried" in window.summary.text()
    assert window.manifest is None
    assert window.hits == []
    assert window.release_boxes["dr9"].isChecked()
    assert not window.release_boxes["dr10"].isChecked()
    assert window.search_btn.isEnabled()
    assert window.sky.url().toString() in ("", "about:blank")


@pytest.mark.parametrize("width,height", [(1366, 768), (1600, 900), (1920, 1080)])
def test_window_resizes_without_losing_core_widgets(offline_explorer, qtbot, width, height):
    window = offline_explorer
    window.resize(width, height)
    qtbot.wait(20)
    assert window.centralWidget() is not None
    assert window.search_btn.isVisible()
    assert window.sky.isVisible()
    assert window.bricks.isVisible()
    assert window.files.isVisible()
    assert window.size().width() == width
    assert window.size().height() == height


def test_filtering_does_not_silently_deselect_hidden_files(offline_explorer):
    window = offline_explorer
    def entry(path, kind, band):
        return {
            "relative_path": path, "url": f"https://example.invalid/{path}",
            "release": "dr9", "region": "north", "brick": "test-brick",
            "kind": kind, "band": band,
            "evidence": "UNVERIFIED_URL", "size_bytes": None,
        }

    window.manifest = {"products": [
        entry("r-image.fits", "image", "r"),
        entry("g-image.fits", "image", "g"),
        entry("maskbits.fits", "maskbits", None),
    ]}
    window._fill_files()
    assert window._selected() == {
        "r-image.fits", "g-image.fits", "maskbits.fits",
    }
    window.band_filter.setCurrentText("r")
    assert window.files.isRowHidden(1)
    assert not window.files.isRowHidden(2)  # shared MASKBITS
    window._set_visible_selection(False)
    # Hidden g image stays selected, visible r + shared mask are cleared.
    assert window._selected() == {"g-image.fits"}
    assert "1 selected" in window.selected_label.text()
