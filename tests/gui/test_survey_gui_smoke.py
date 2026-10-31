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
    assert window.bricks.rowCount() == 0
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
    # Quick smoke coverage on a default-sized floating test window:
    # the *actual WebEngine widget* must occupy the largest share.
    # The dedicated screenshot suite separately enforces >=55% for the
    # whole scientific preview pane at 1366, 1600 and 1920 widths.
    assert window.sky.width() >= window.width() * 0.50


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


def test_release_labels_are_unclipped_on_compact_navigation(offline_explorer, qtbot):
    """The release selector uses a full-width form row rather than a split label row."""
    window = offline_explorer
    window.resize(1366, 768)
    qtbot.wait(30)
    from PySide6.QtCore import QPoint
    nav = window.navigation_scroll.viewport()
    nav_width = nav.width()
    labels = []
    for dr in ("dr9", "dr10", "dr11"):
        box = window.release_boxes[dr]
        assert box.text() == dr.upper()
        assert box.isVisible()
        assert box.width() >= box.sizeHint().width()
        pos = box.mapTo(nav, QPoint(0, 0))
        assert pos.x() >= 0
        assert pos.x() + box.width() <= nav_width
        labels.append((pos.x(), pos.x() + box.width()))
    assert labels[0][1] <= labels[1][0]
    assert labels[1][1] <= labels[2][0]


def test_target_entry_is_real_pasteable_text_not_spinboxes(offline_explorer, qtbot):
    from PySide6.QtWidgets import QApplication, QLineEdit, QDoubleSpinBox
    from PySide6.QtCore import Qt
    from lsbpipe.gui.target_input import parse_ra_deg

    window = offline_explorer
    for widget in (window.ra, window.dec, window.field_width):
        assert isinstance(widget, QLineEdit)
        assert not isinstance(widget, QDoubleSpinBox)
        assert widget.isVisible()
        assert widget.isEnabled()

    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    try:
        clipboard.setText("10:19:05")
        window.ra.setFocus()
        window.ra.selectAll()
        qtbot.keyClick(window.ra, Qt.Key_V, modifier=Qt.ControlModifier)
        assert window.ra.text() == "10:19:05"
        assert window.ra.value() == pytest.approx(parse_ra_deg("10:19:05"))
        window.dec.setText("46,4537")
        window.field_width.setText("0.25 deg")
        field = window._field()
        assert field.ra_deg == pytest.approx(parse_ra_deg("10:19:05"))
        assert field.dec_deg == pytest.approx(46.4537)
        assert field.width_arcmin == pytest.approx(15)
    finally:
        clipboard.setText(previous)


def test_coordinate_pair_button_pastes_both_fields_and_preserves_width(offline_explorer):
    from PySide6.QtWidgets import QApplication
    window = offline_explorer
    clip = QApplication.clipboard()
    previous = clip.text()
    try:
        window.field_width.setText("900 arcsec")
        clip.setText("154,7709 46,4537")
        window.paste_target_btn.click()
        assert window.ra.value() == pytest.approx(154.7709)
        assert window.dec.value() == pytest.approx(46.4537)
        assert window.field_width.value() == pytest.approx(15)
        assert "Pasted ICRS" in window.target_hint.text()
    finally:
        clip.setText(previous)


def test_invalid_coordinate_blocks_archive_search_without_clamping(
        offline_explorer, qtbot):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    window = offline_explorer
    window.ra.setText("361")
    with patch.object(QMessageBox, "warning") as warning, \
            patch.object(window, "_start") as start:
        window.search()
    assert warning.call_count == 1
    assert "RA" in warning.call_args.args[2]
    start.assert_not_called()
    assert window.ra.text() == "361"  # no QDoubleSpinBox clamping
    assert window.ra.property("invalid") is True
    window.ra.setFocus()
    qtbot.keyClicks(window.ra, "0")
    window.ra.setText("154.7709")
    assert window._field().ra_deg == pytest.approx(154.7709)


def test_invalid_pair_paste_does_not_partially_overwrite_inputs(offline_explorer):
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication, QMessageBox
    window = offline_explorer
    original = (window.ra.text(), window.dec.text())
    clip = QApplication.clipboard()
    previous = clip.text()
    try:
        clip.setText("154.7709 99")
        with patch.object(QMessageBox, "warning") as warning:
            window.paste_coordinate_pair()
        warning.assert_called_once()
        assert (window.ra.text(), window.dec.text()) == original
    finally:
        clip.setText(previous)


def test_pair_paste_retains_submilliarcsecond_decimal_precision(offline_explorer):
    from PySide6.QtWidgets import QApplication
    window = offline_explorer
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    try:
        clipboard.setText("154.7709123456\t+46.4537123456")
        window.paste_target_btn.click()
        assert window.ra.value() == pytest.approx(154.7709123456, abs=5e-11)
        assert window.dec.value() == pytest.approx(46.4537123456, abs=5e-11)
    finally:
        clipboard.setText(previous)



def test_search_area_defaults_to_five_arcmin_and_has_clear_name(offline_explorer):
    from urllib.parse import urlparse, parse_qs
    window = offline_explorer
    assert window.field_width.value() == pytest.approx(5.0)
    assert window.field_width.accessibleName() == (
        "Square search area side length, arcminutes")
    assert window.field_width.toolTip().startswith(
        "Side length of the square search area")
    params = parse_qs(urlparse(window._viewer_link()).query)
    assert "poly" in params
    assert len(params["poly"][0].split(",")) == 34


def test_search_outline_tracks_edited_coordinates_and_width(offline_explorer):
    from urllib.parse import urlparse, parse_qs
    from lsbpipe.surveys.legacy import SkyField
    from lsbpipe.surveys.viewer import field_polygon_query
    window = offline_explorer
    window.ra.setText("40.096208")
    window.dec.setText("+1.229250")
    window.field_width.setText("0.25 deg")
    params = parse_qs(urlparse(window._viewer_link()).query)
    assert params["poly"] == [field_polygon_query(
        SkyField(40.096208, 1.22925, 15))]
    assert params['mark'] == ['40.0962080,1.2292500']


def test_brick_preview_preserves_target_overlay(offline_explorer, qtbot):
    from unittest.mock import patch
    from PySide6.QtCore import QUrl
    from urllib.parse import urlparse, parse_qs
    from lsbpipe.surveys.legacy import SkyField
    from lsbpipe.surveys.viewer import field_polygon_query
    window = offline_explorer
    window.ra.setText("40.096208")
    window.dec.setText("+1.229250")
    window.field_width.setText("5")
    apply_offline_state(window, "S1")
    target = SkyField(40.096208, 1.22925, 5)
    with patch.object(window.sky, 'load') as loader:
        window.preview_brick(0, 0)
    loader.assert_called_once()
    sent = loader.call_args.args[0]
    assert isinstance(sent, QUrl)
    params = parse_qs(urlparse(sent.toString()).query)
    assert params["poly"][0].split(";")[0] == field_polygon_query(target)
    # The embedded page keeps only the target polygon in the URL;
    # full brick outlines use the live Leaflet layer to avoid auto-zoom.
    assert len(params["poly"][0].split(";")) == 1
    assert "mark" not in params


def test_show_target_marks_5_arcmin_square_and_updates_viewer_heading(
        offline_explorer):
    from unittest.mock import patch
    from urllib.parse import urlparse, parse_qs
    window = offline_explorer
    assert window.field_width.value() == pytest.approx(5.)
    with patch.object(window.sky, 'load') as loader:
        window.show_viewer()
    loader.assert_called_once()
    args = parse_qs(urlparse(loader.call_args.args[0].toString()).query)
    assert args['zoom'] == ['13']
    assert 'poly' in args
    assert '5′ × 5′' in window.preview_label.text()
    assert 'not calibrated FITS' in window.preview_label.text()


def test_field_size_change_affects_search_polygon_and_map_framing(
        offline_explorer):
    from urllib.parse import urlparse, parse_qs
    window = offline_explorer
    window.field_width.setText('15 arcmin')
    args = parse_qs(urlparse(window._viewer_link()).query)
    assert args['zoom'] == ['11']
    assert len(args['poly'][0].split(',')) == 34
    window.field_width.setText('1 arcmin')
    args_small = parse_qs(urlparse(window._viewer_link()).query)
    assert args_small['zoom'] == ['15']
    assert args_small['poly'] != args['poly']


def test_redundant_viewer_controls_and_summary_removed(offline_explorer):
    w = offline_explorer
    assert not hasattr(w, "diagnostic_btn")
    assert not hasattr(w, "web_scale")
    assert not hasattr(w, "summary")
    assert w.bricks.isVisible()


def test_brick_selection_preserves_embedded_viewer_zoom_and_native_browser_fallback(offline_explorer):
    from urllib.parse import urlparse, parse_qs
    from unittest.mock import patch
    w = offline_explorer
    apply_offline_state(w, "S1")
    # The live Leaflet brick layer handles hover/click in the embedded page.
    # URL poly= must only include target so that the hosted app does NOT
    # auto-fit the 4 entire bricks and zoom away from the 5' target field.
    unselected = parse_qs(urlparse(w._viewer_link()).query)
    assert len(unselected["poly"][0].split(";")) == 1
    assert unselected["zoom"] == ["13"]
    with patch.object(w.sky, "load") as loader:
        w.bricks.selectRow(0)
    loader.assert_not_called()
    assert parse_qs(urlparse(w._viewer_link()).query) == unselected
    # External browser uses official static multi-poly when JS integration
    # cannot be installed from our own Qt process.
    browser = parse_qs(urlparse(w._viewer_link(include_bricks=True)).query)
    assert len(browser["poly"][0].split(";")) == 7
    w.bricks.clearSelection()
    assert len(parse_qs(urlparse(
        w._viewer_link(include_bricks=True)).query)["poly"][0].split(";")) == 5


def test_brick_hover_and_leave_are_fast_and_do_not_reload_remote_map(offline_explorer):
    """Table hover is a Leaflet style update, never a webpage navigation."""
    from unittest.mock import patch
    from PySide6.QtCore import QEvent
    window = offline_explorer
    apply_offline_state(window, "S1")
    with patch.object(window, "_run_brick_js") as js, patch.object(
            window.sky, "load") as load:
        window._hover_brick_row(0, 2)
        assert 'hover("0")' in js.call_args.args[0]
        window.eventFilter(window.bricks.viewport(), QEvent(QEvent.Leave))
        assert 'hover(null)' in js.call_args.args[0]
        window.bricks.selectRow(1)
        assert 'select(' in js.call_args.args[0]
        load.assert_not_called()


def test_leaflet_script_refuses_untrusted_web_navigation(offline_explorer):
    from unittest.mock import patch
    from PySide6.QtCore import QUrl
    window = offline_explorer
    apply_offline_state(window, "S1")
    page = window.sky
    with patch.object(page, "url", return_value=QUrl(
            "https://untrusted.example/viewer/")):
        with patch.object(page, "page", create=True) as p:
            window._run_brick_js("alert('must not run')")
            p.assert_not_called()


def test_brick_geometry_does_not_follow_edited_target_as_stale_coverage(offline_explorer):
    from urllib.parse import urlparse, parse_qs
    w = offline_explorer
    apply_offline_state(w, "S1")
    assert w._brick_result_matches_target()
    assert len(parse_qs(urlparse(
        w._viewer_link(include_bricks=True)).query)["poly"][0].split(";")) == 5
    w.ra.setText("40.096208")
    assert not w._brick_result_matches_target()
    # Only the newly requested target area remains on map; previous
    # unrelated archive brick polygons must not claim to cover it.
    assert len(parse_qs(urlparse(
        w._viewer_link(include_bricks=True)).query)["poly"][0].split(";")) == 1



def test_science_workbench_is_a_separate_tab_preserving_archive_and_viewer(
        offline_explorer, qtbot):
    w = offline_explorer
    assert w.inspector_tabs.count() == 2
    assert w.inspector_tabs.tabText(0) == "Archive files"
    assert w.inspector_tabs.tabText(1) == "Science QA"
    assert w.preview_tabs.count() == 3
    assert w.preview_tabs.currentWidget() is w.sky
    assert w.files.columnCount() == 4
    assert w.save_btn.isEnabled()
    assert w.download_btn.isEnabled()
    assert not w.science_qa.buttons["square"].isEnabled()
    assert not w.science_qa.buttons["compare"].isEnabled()
    apply_offline_state(w, "S3")
    w.inspector_tabs.setCurrentWidget(w.science_qa)
    qtbot.wait(20)
    assert w.science_qa.selection_label.text().startswith("DR9 · north")
    assert w.science_qa.current_band() == "r"
    assert all(b.isEnabled() for b in w.science_qa.buttons.values())
    assert w.sky.isVisible()  # local QA analysis does not hide the sky viewer


def test_science_qa_uses_selected_brick_and_pasted_target_without_requery(
        offline_explorer, tmp_path):
    from unittest.mock import patch
    w = offline_explorer
    apply_offline_state(w, "S3")
    folder = tmp_path/"user-pilot"
    w.science_qa.set_data_root(folder, region="north")
    assert w.science_qa.directory.text() == str(folder)
    assert w.science_qa.north_directory.text() == str(folder)
    evidence = {}
    def inline(action, finished, on_error=None):
        evidence["threaded"] = False
        assert callable(on_error)
        finished(action(None))
    with patch.object(w, "_start", side_effect=inline), patch(
            "lsbpipe.gui.survey_explorer.run_science_qa_backend") as core:
        core.side_effect = lambda *a, **k: {
            "action":"square", "status":"DIAGNOSTIC_NOT_ADOPTED",
            "summary":"Original square: 98% instrumentally usable",
            "path":str(tmp_path/"qa-reports"/"square.json"),
            "is_figure":False, "context":k["context"],
            "provenance":("dr9","north",w.hits[0].name,"r")}
        w.start_science_qa("square")
        core.assert_called_once()
    kws = core.call_args.kwargs
    assert kws["directory"] == str(folder)
    assert kws["hit"] == w.hits[0]
    assert kws["field"] == w._field()
    assert kws["band"] == "r"
    assert "98%" in w.science_qa.result.toPlainText()
    assert w.science_qa.open_report.isEnabled()
    assert w.preview_tabs.currentWidget() is w.sky


def test_science_qa_never_analyzes_stale_archive_selection(
        offline_explorer):
    from unittest.mock import patch
    w = offline_explorer
    apply_offline_state(w, "S3")
    w.science_qa.directory.setText("Z:/old-data")
    w.ra.setText("40.096208")
    with patch("lsbpipe.gui.survey_explorer.run_science_qa_backend") as core, \
         patch("lsbpipe.gui.survey_explorer.QMessageBox.warning") as warning:
        w.start_science_qa("square")
    warning.assert_called_once()
    core.assert_not_called()


def test_local_original_fits_map_opens_in_central_preview_tab(
        offline_explorer, tmp_path, qtbot):
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt
    w = offline_explorer
    apply_offline_state(w, "S3")
    image = tmp_path/"QA_figure.png"
    pix = QPixmap(200,100)
    pix.fill(Qt.white)
    assert pix.save(str(image), "PNG")
    w._science_qa_ready({
        "action":"figure", "status":"DIAGNOSTIC_NOT_ADOPTED",
        "summary":"IMAGE / INVVAR / NEXP / MASKBITS",
        "path":str(image), "is_figure":True,
        "provenance":("dr9","north",w.hits[0].name,"r")})
    qtbot.wait(20)
    assert w.preview_tabs.currentWidget() is w.qa_image_scroll
    assert w._qa_pixmap is not None
    assert w.qa_image.pixmap() is not None
    assert "LOCAL ORIGINAL FITS" in w.preview_label.text()
    w.preview_tabs.setCurrentWidget(w.sky)
    assert "REMOTE PREVIEW" in w.preview_label.text()


def test_science_qa_disables_unsafe_paired_comparison_without_two_roots(
        offline_explorer):
    from unittest.mock import patch
    w = offline_explorer
    apply_offline_state(w, "S3")
    with patch("lsbpipe.gui.survey_explorer.QMessageBox.information") as info, \
         patch("lsbpipe.gui.survey_explorer.run_science_qa_backend") as core:
        w.start_science_qa("compare")
    info.assert_called_once()
    core.assert_not_called()


def test_qa_fields_remain_scrollable_at_laptop_size(offline_explorer, qtbot):
    w = offline_explorer
    apply_offline_state(w, "S3")
    w.resize(1366, 768)
    w.inspector_tabs.setCurrentWidget(w.science_qa)
    qtbot.wait(30)
    assert w.science_qa.width() > 250
    assert w.science_qa.buttons["square"].isEnabled()
    assert w.sky.width() >= w.width() * 0.55



def test_science_run_rejects_changed_radii_even_if_restored(
        offline_explorer, tmp_path):
    from unittest.mock import patch
    w = offline_explorer
    apply_offline_state(w, "S3")
    w.science_qa.directory.setText(str(tmp_path/"original-data"))
    pending = []
    def defer(action, callback, on_error=None):
        pending.append((action, callback, on_error))
    with patch.object(w, "_start", side_effect=defer):
        w.start_science_qa("square")
    assert len(pending) == 1
    snapshot = w._current_science_context("square")
    assert snapshot is not None
    previous_ticket = w._qa_generation
    value = w.target_radius.value()
    w.target_radius.setValue(value + 1.)
    w.target_radius.setValue(value)
    assert w._qa_generation > previous_ticket
    assert w._current_science_context("square") == snapshot
    pending[0][1]({
        "action":"square", "status":"DIAGNOSTIC_NOT_ADOPTED",
        "summary":"STALE INCORRECT SCIENCE",
        "path":str(tmp_path/"old.json"), "is_figure":False,
        "context":snapshot,
        "provenance":("dr9","north",w.hits[0].name,"r")})
    assert "STALE" not in w.science_qa.result.toPlainText()
    assert not w.science_qa.open_report.isEnabled()


def test_editing_fits_folder_invalidates_previous_saved_result(
        offline_explorer, tmp_path):
    w = offline_explorer
    apply_offline_state(w, "S3")
    w.science_qa.show_result({
        "summary":"Last old aperture", "path":str(tmp_path/"old.json"),
        "is_figure":False})
    assert w.science_qa.open_report.isEnabled()
    ticket = w._qa_generation
    w.science_qa.directory.setText(str(tmp_path/"new-root"))
    assert w._qa_generation > ticket
    assert not w.science_qa.open_report.isEnabled()
    assert "Last old aperture" not in w.science_qa.result.toPlainText()


def test_changing_band_or_north_folder_invalidates_loaded_fits(
        offline_explorer, tmp_path):
    from tests.gui.test_fits_explorer_gui import synthetic_dataset
    w = offline_explorer
    apply_offline_state(w, "S3")
    w.fits_explorer.set_dataset(synthetic_dataset())
    assert w.fits_explorer._dataset is not None
    w.science_qa.north_directory.setText(str(tmp_path/"other-north"))
    assert w.fits_explorer._dataset is None
    assert not w.science_qa.open_report.isEnabled()
    w.fits_explorer.set_dataset(synthetic_dataset())
    # The synthetic selected brick has r/g/z band inventory.
    old = w.science_qa.current_band()
    next_band = "g" if old != "g" else "r"
    w.science_qa.band.setCurrentText(next_band)
    assert w.fits_explorer._dataset is None
