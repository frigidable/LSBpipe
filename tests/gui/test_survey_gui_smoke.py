"""Offline Survey Explorer behavior smoke tests; no archive or Viewer I/O."""
from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from scripts.offline_gui_fixture import offline_legacy_viewer, apply_offline_state

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
    assert not window.data_drawer.isVisible()
    actual = window.size()
    screen = window.screen()
    assert screen is not None
    available = screen.availableGeometry()

    # A live Windows test runs on a physical display. At 150% scaling,
    # a 1920x1080 *logical* request needs 2880x1620 physical pixels and
    # the window manager can legitimately cap the window to the screen.
    # Offscreen CI, not a user's physical monitor, verifies all three
    # *exact* requested capture dimensions via capture_gui_baseline.py.
    frame = window.frameGeometry()
    client = window.geometry()
    decoration_w = max(0, frame.width() - client.width())
    decoration_h = max(0, frame.height() - client.height())
    can_fit = (
        width + decoration_w <= available.width() and
        height + decoration_h <= available.height()
    )
    if can_fit:
        assert actual.width() == width
        assert actual.height() == height
    else:
        # The OS may clamp to visible desktop geometry. This is not a
        # regression in our adaptive Qt layouts.
        assert 0 < actual.width() <= width
        assert 0 < actual.height() <= height
        assert actual.width() >= min(width, available.width()) - 80
        assert actual.height() >= min(height, available.height()) - 80

    # The scientific preview must dominate the *actual* visible window.
    assert window.sky.width() >= actual.width() * 0.55


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


def test_data_workbench_is_progressive_and_essential_actions_remain_reachable(
        offline_explorer, qtbot):
    window = offline_explorer
    assert not window.data_drawer.isVisible()
    assert window.data_toggle.isVisible()
    window.data_toggle.click()
    qtbot.wait(20)
    assert window.data_drawer.isVisible()
    assert window.files.isVisible()
    assert window.files.minimumHeight() >= 148
    assert window.qa_toggle.isVisible()
    assert not window.qa_widget.isVisible()
    window.qa_toggle.click()
    qtbot.wait(20)
    assert window.qa_widget.isVisible()
    assert window.qa_btn.isVisible()
    assert window.download_btn.isVisible()
    window.qa_toggle.click()
    qtbot.wait(20)
    assert not window.qa_widget.isVisible()
    window.data_toggle.click()
    qtbot.wait(20)
    assert not window.data_drawer.isVisible()
    assert window.message.isVisible()
    assert window.bar.isVisible()


@pytest.mark.parametrize("state,expected", [
    ("S1", 88), ("S2", 88), ("S3", 4)
])
def test_offline_population_scenarios_and_selection(offline_explorer, state, expected):
    window = offline_explorer
    apply_offline_state(window, state)
    assert len(window.hits) == 8
    assert len(window.manifest["products"]) == 88
    assert len(window._selected()) == expected
    assert window.data_drawer.isVisible()
    assert window.files.isVisible()
    if state == "S2":
        assert sum(window.files.isRowHidden(i)
                   for i in range(window.files.rowCount())) > 0
    if state == "S3":
        selected = [
            item for item in window.manifest["products"]
            if item["relative_path"] in window._selected()
        ]
        assert {item["kind"] for item in selected} == {
            "image", "invvar", "nexp", "maskbits"}
        assert {item["brick"] for item in selected} == {"1544p465"}


def test_navigation_scroll_keeps_dense_controls_accessible(offline_explorer, qtbot):
    window = offline_explorer
    window.resize(1366, 768)
    qtbot.wait(30)
    assert window.navigation_scroll.width() <= 425
    assert window.navigation_scroll.verticalScrollBar() is not None
    assert window.search_btn.isVisible()
    assert window.data_toggle.isVisible()


def test_selection_can_explicitly_clear_hidden_and_visible_products(offline_explorer):
    window = offline_explorer
    apply_offline_state(window, "S1")
    assert len(window._selected()) == 88
    window.band_filter.setCurrentText("r")
    window.kind_filter.setCurrentText("image")
    assert sum(window.files.isRowHidden(i)
               for i in range(window.files.rowCount())) > 0
    window.clear_all_btn.click()
    assert window._selected() == set()
    assert "0 selected" in window.selected_label.text()
    # The dedicated science preset then selects exactly four paths from
    # one chosen release/brick, regardless of active table filters.
    window.bricks.selectRow(0)
    from unittest.mock import patch
    from lsbpipe.gui import survey_explorer as gui
    with patch.object(gui.QInputDialog, "getItem", return_value=("r", True)):
        window.select_science_quartet()
    assert len(window._selected()) == 4


def test_nexp_column_is_prioritized_and_qa_starts_collapsed(offline_explorer):
    from PySide6.QtWidgets import QHeaderView
    window = offline_explorer
    header = window.bricks.horizontalHeader()
    for column in (0, 1, 2):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeToContents
    assert header.sectionResizeMode(3) == QHeaderView.Stretch
    assert window.bricks.horizontalHeaderItem(3).text() == "NEXP g/r/i/z"
    assert not window.qa_widget.isVisible()
    assert window.files.minimumHeight() >= 148


def test_science_inspector_docks_to_right_of_remote_viewer(offline_explorer, qtbot):
    from PySide6.QtCore import Qt, QPoint
    window = offline_explorer
    assert window.dockWidgetArea(window.data_drawer) == Qt.RightDockWidgetArea
    apply_offline_state(window, "S1")
    qtbot.wait(40)
    assert window.data_drawer.isVisible()
    assert window.files.columnCount() == 4
    assert window.files.horizontalHeaderItem(1).text() == "Product / provenance"
    assert window.files.horizontalHeaderItem(2).text() == "HTTP"
    assert window.files.rowCount() == 88
    inspector_x = window.data_drawer.mapTo(window, QPoint(0, 0)).x()
    preview_x = window.sky.mapTo(window, QPoint(0, 0)).x()
    assert inspector_x > preview_x
    assert window.sky.width() >= window.width() * 0.55


def test_http_status_abbreviations_keep_original_evidence(offline_explorer):
    window = offline_explorer
    apply_offline_state(window, "S1")
    row = 0
    first = window.manifest["products"][row]
    item = window.files.item(row, 1)
    assert first["brick"] in item.text()
    assert first["release"].upper() in item.text()
    assert first["kind"] in item.text()
    assert first["relative_path"] in item.toolTip()
    assert window.files.item(row, 2).text() == "?"
    r = {first["relative_path"]: {
        "evidence": "HTTP_OK", "size_bytes": 1048576}}
    window._receive_verification(r)
    assert window.files.item(row, 2).text() == "OK"
    assert window.files.item(row, 3).text() == "1"
    assert first["relative_path"] in window.files.item(row, 2).toolTip()
