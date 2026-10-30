"""PySide6 survey explorer: official Legacy Sky Viewer and archive downloads.

Requires the optional 'gui' extra, a working QtWebEngine and network access
for the hosted Legacy Sky Viewer and the Legacy archive. No GUI dependency is
imported by the scientific numerical core.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QLineEdit,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QInputDialog,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QDockWidget, QScrollArea, QSizePolicy)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

from lsbpipe.surveys.legacy import (
    SkyField, build_manifest, discover_bricks, save_manifest)
from lsbpipe.surveys.transfer import probe_product, download_manifest
from lsbpipe.surveys.viewer import viewer_url
from lsbpipe.surveys.selection import matching_product, summarize_selection
from lsbpipe.gui.target_input import (
    parse_ra_deg, parse_dec_deg, parse_width_arcmin, parse_coordinate_pair)


class _DiagnosticWebPage(QWebEnginePage):
    """Collect WebEngine JavaScript console output for on-device diagnostics."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recent_console = []

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        self.recent_console.append(
            f"{str(level)}: {message} ({source_id}:{line_number})")
        del self.recent_console[:-20]


class TaskWorker(QObject):
    completed = Signal(object)
    progress = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: Callable):
        super().__init__()
        self.task = task

    @Slot()
    def run(self):
        try:
            self.completed.emit(self.task(self.progress.emit))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')
        finally:
            self.finished.emit()


class TargetTextField(QLineEdit):
    """Ordinary pasteable text field with strict read-time parsing.

    Unlike QDoubleSpinBox it has no spin buttons, locale-dependent input
    filtering, or silently clamped values. Invalid drafts remain editable.
    """

    def __init__(self, initial: float, parser, *, decimals: int = 6, parent=None):
        super().__init__(parent)
        self._parser = parser
        self._decimals = decimals
        self.setProperty("targetInput", True)
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(35)
        self.setValue(initial)
        self.editingFinished.connect(self._validate_quietly)
        self.textEdited.connect(self._clear_invalid)

    def value(self) -> float:
        return self._parser(self.text())

    def setValue(self, value: float):
        self.setText(f"{float(value):.{self._decimals}f}".rstrip("0").rstrip("."))
        self._clear_invalid()

    def _mark_invalid(self, reason: str):
        if not self.property("invalid"):
            self._normal_tooltip = self.toolTip()
        self.setProperty("invalid", True)
        self.setToolTip(reason)
        self._refresh_style()

    def _clear_invalid(self, *_):
        if self.property("invalid"):
            self.setProperty("invalid", False)
            self.setToolTip(getattr(self, "_normal_tooltip", ""))
            self._refresh_style()

    def _refresh_style(self):
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _validate_quietly(self):
        try:
            self.value()
        except ValueError as exc:
            self._mark_invalid(str(exc))
        else:
            self._clear_invalid()


class SurveyExplorer(QMainWindow):
    """Initial desktop flow: ICRS target -> release bricks -> file plan -> download."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LSBpipe — Legacy Survey Explorer (experimental)")
        self.resize(1460, 880)
        self.hits = []
        self.manifest = None
        self.worker = None
        self.thread = None
        self._build_ui()

    def _build_ui(self):
        # Geometry/legibility rules only; retain OS light/dark palette.
        # The hosted Legacy web page is visually independent of this stylesheet.
        self.setStyleSheet(
            "QPushButton, QComboBox, QDoubleSpinBox { min-height: 27px; } "
            "QPushButton { padding-left: 6px; padding-right: 6px; } "
            "QTableWidget { font-size: 12px; }"
            "QLineEdit[targetInput=\"true\"] { "
            "min-height: 33px; padding: 1px 9px; border-radius: 6px; "
            "border: 1px solid palette(mid); font-size: 13px; }"
            "QLineEdit[targetInput=\"true\"]:focus { "
            "border: 2px solid palette(highlight); padding-left: 8px; }"
            "QLineEdit[invalid=\"true\"] { border: 2px solid #d56b69; }"
        )
        split = QSplitter(Qt.Horizontal, self)
        left = QWidget()
        left.setObjectName("survey_navigation")
        left.setMinimumWidth(270)
        panel = QVBoxLayout(left)
        panel.setContentsMargins(10, 10, 10, 10)
        panel.setSpacing(8)

        target_box = QGroupBox("Target · ICRS")
        target_form = QVBoxLayout(target_box)
        target_form.setContentsMargins(10, 12, 10, 10)
        target_form.setSpacing(5)

        self.ra = TargetTextField(154.7709, parse_ra_deg, decimals=10)
        self.ra.setObjectName("target_ra_input")
        self.ra.setAccessibleName("Right ascension ICRS")
        self.ra.setPlaceholderText("154.7709 or 10:19:05")
        self.ra.setToolTip("Paste ICRS RA in decimal degrees, or hh:mm:ss")
        self.dec = TargetTextField(46.4537, parse_dec_deg, decimals=10)
        self.dec.setObjectName("target_dec_input")
        self.dec.setAccessibleName("Declination ICRS")
        self.dec.setPlaceholderText("+46.4537 or +46:27:13")
        self.dec.setToolTip("Paste ICRS Dec in decimal degrees, or ±dd:mm:ss")
        self.field_width = TargetTextField(5.0, parse_width_arcmin, decimals=2)
        self.field_width.setObjectName("target_field_width_input")
        self.field_width.setAccessibleName("Square search area side length, arcminutes")
        self.field_width.setPlaceholderText("5  ·  0.0833 deg  ·  300 arcsec")
        self.field_width.setToolTip(
            "Side length of the square search area, centered on RA/Dec. "
            "Default units: arcmin; also accepts deg or arcsec. "
            "The outline is shown in the Legacy Viewer.")

        for label, field in (
            ("RA · degrees / hh:mm:ss", self.ra),
            ("Dec · degrees / ±dd:mm:ss", self.dec),
            ("Search area · side length (arcmin)", self.field_width),
        ):
            caption = QLabel(label)
            caption.setBuddy(field)
            target_form.addWidget(caption)
            target_form.addWidget(field)

        self.paste_target_btn = QPushButton("Paste RA + Dec")
        self.paste_target_btn.setToolTip(
            "Paste a pair from the clipboard, e.g. 154.7709 46.4537 "
            "or 10:19:05 +46:27:13")
        self.paste_target_btn.clicked.connect(self.paste_coordinate_pair)
        target_form.addWidget(self.paste_target_btn)
        self.target_hint = QLabel("Decimal degrees, decimal comma or sexagesimal")
        self.target_hint.setObjectName("target_validation_hint")
        self.target_hint.setWordWrap(True)
        self.target_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        target_form.addWidget(self.target_hint)
        for field in (self.ra, self.dec, self.field_width):
            field.textEdited.connect(self._target_edited)

        target_form.addWidget(QLabel("Data releases"))
        releases_container = QWidget()
        releases = QHBoxLayout(releases_container)
        releases.setContentsMargins(0, 0, 0, 0)
        releases.setSpacing(10)
        self.release_boxes = {}
        for dr in ("dr9", "dr10", "dr11"):
            check = QCheckBox(dr.upper())
            check.setChecked(dr == "dr9")
            check.setToolTip(f"Include official Legacy Survey {dr.upper()} data")
            self.release_boxes[dr] = check
            releases.addWidget(check)
        releases.addStretch(1)
        target_form.addWidget(releases_container)
        actions = QHBoxLayout()
        self.search_btn = QPushButton("Find data")
        self.search_btn.setToolTip("Discover Legacy archive bricks for this ICRS field")
        self.search_btn.clicked.connect(self.search)
        actions.addWidget(self.search_btn)
        self.center_btn = QPushButton("Show target")
        self.center_btn.clicked.connect(self.show_viewer)
        actions.addWidget(self.center_btn)
        target_form.addLayout(actions)
        secondary_actions = QHBoxLayout()
        self.diagnostic_btn = QPushButton("Viewer status")
        self.diagnostic_btn.clicked.connect(self.show_viewer_diagnostics)
        secondary_actions.addWidget(self.diagnostic_btn)
        self.external_btn = QPushButton("Open in browser")
        self.external_btn.clicked.connect(self.open_viewer_in_browser)
        secondary_actions.addWidget(self.external_btn)
        target_form.addLayout(secondary_actions)
        panel.addWidget(target_box)

        viewer_controls = QHBoxLayout()
        viewer_controls.addWidget(QLabel("Viewer"))
        self.view_mode = QComboBox()
        self.view_mode.addItem("Image", "image")
        self.view_mode.addItem("Tractor model", "model")
        self.view_mode.addItem("Residual", "resid")
        self.view_mode.currentIndexChanged.connect(self.show_viewer)
        viewer_controls.addWidget(self.view_mode, 2)
        viewer_controls.addWidget(QLabel("Web scale"))
        self.web_scale = QDoubleSpinBox()
        self.web_scale.setRange(0.55, 1.30)
        self.web_scale.setSingleStep(0.05)
        self.web_scale.setDecimals(2)
        self.web_scale.setValue(0.80)
        self.web_scale.setToolTip("Qt page scale; not astronomical map zoom")
        self.web_scale.valueChanged.connect(self._apply_web_scale)
        viewer_controls.addWidget(self.web_scale)
        panel.addLayout(viewer_controls)
        self.data_toggle = QPushButton("Files and coverage")
        self.data_toggle.setAccessibleName("Show or hide file selection and coverage")
        self.data_toggle.setToolTip(
            "Open the download and local FITS coverage workbench")
        self.data_toggle.clicked.connect(self._toggle_data_drawer)
        panel.addWidget(self.data_toggle)

        self.summary = QLabel("No catalogs queried yet; no coverage claims.")
        self.summary.setWordWrap(True)
        panel.addWidget(self.summary)
        self.bricks = QTableWidget(0, 4)
        self.bricks.setAccessibleName("Release bricks and median exposure diagnostics")
        self.bricks.setHorizontalHeaderLabels(["DR", "Region", "Brick", "NEXP g/r/i/z"])
        # Avoid giving narrow DR/region columns the same width as NEXP.
        for column in (0, 1, 2):
            self.bricks.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents)
        self.bricks.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self.bricks.horizontalHeaderItem(3).setToolTip(
            "Median exposures per brick in g, r, i, z; not pixel coverage")
        self.bricks.setMinimumHeight(140)
        panel.addWidget(QLabel("Release-covered intersecting bricks"))
        panel.addWidget(self.bricks, 1)
        self.bricks.cellDoubleClicked.connect(self.preview_brick)
        panel.addWidget(QLabel("Double-click a brick to preview its center"))
        panel.addStretch(1)

        # File selection and coverage are a separate, collapsible task surface.
        # They no longer set a 900+ px minimum width on the survey navigation.
        data_container = QWidget()
        data_panel = QVBoxLayout(data_container)
        data_panel.setContentsMargins(10, 8, 10, 8)
        data_panel.setSpacing(6)

        # A vertical inspector should not behave like a wide spreadsheet.
        data_panel.addWidget(QLabel("Archive product set"))
        self.profile = QComboBox()
        self.profile.addItems([
            "Standard: image, invvar, maskbits, nexp, Tractor",
            "Extended: +psfsize, depth, galdepth, model"])
        self.profile.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.profile.setMinimumContentsLength(13)
        self.profile.currentIndexChanged.connect(self._apply_profile)
        data_panel.addWidget(self.profile)
        self.verify_btn = QPushButton("Check selected URLs")
        self.verify_btn.clicked.connect(self.verify)
        data_panel.addWidget(self.verify_btn)

        filters = QHBoxLayout()
        self.brick_filter = QComboBox()
        self.brick_filter.addItem("All bricks")
        self.brick_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.brick_filter, 2)
        self.band_filter = QComboBox()
        self.band_filter.addItems(["All bands", "g", "r", "i", "z"])
        self.band_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.band_filter, 1)
        data_panel.addLayout(filters)
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(
            ["All products", "image", "invvar", "maskbits", "nexp",
             "tractor", "psfsize", "depth", "galdepth", "model"])
        self.kind_filter.currentIndexChanged.connect(self._filter_products)
        data_panel.addWidget(self.kind_filter)

        self.selected_label = QLabel("0 files selected")
        self.selected_label.setWordWrap(True)
        data_panel.addWidget(self.selected_label)
        self.science_btn = QPushButton("Science quartet (4 FITS)")
        self.science_btn.setToolTip(
            "Select IMAGE, INVVAR, NEXP and MASKBITS for one selected brick and band")
        self.science_btn.clicked.connect(self.select_science_quartet)
        data_panel.addWidget(self.science_btn)

        selection_actions = QHBoxLayout()
        self.select_visible_btn = QPushButton("Select visible")
        self.select_visible_btn.clicked.connect(lambda: self._set_visible_selection(True))
        selection_actions.addWidget(self.select_visible_btn)
        self.clear_visible_btn = QPushButton("Clear visible")
        self.clear_visible_btn.clicked.connect(lambda: self._set_visible_selection(False))
        selection_actions.addWidget(self.clear_visible_btn)
        data_panel.addLayout(selection_actions)
        self.clear_all_btn = QPushButton("Clear all (including hidden)")
        self.clear_all_btn.setToolTip(
            "Deselect every file, including entries hidden by the current filters")
        self.clear_all_btn.clicked.connect(self._clear_all_selection)
        data_panel.addWidget(self.clear_all_btn)

        self.files = QTableWidget(0, 4)
        self.files.setAccessibleName(
            "Candidate Legacy FITS files, HTTP evidence and download selections")
        self.files.setHorizontalHeaderLabels(
            ["Use", "Product / provenance", "HTTP", "MiB"])
        self.files.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Fixed)
        self.files.horizontalHeader().resizeSection(0, 37)
        self.files.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.files.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Fixed)
        self.files.horizontalHeader().resizeSection(2, 54)
        self.files.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Fixed)
        self.files.horizontalHeader().resizeSection(3, 43)
        self.files.setTextElideMode(Qt.ElideMiddle)
        self.files.setAlternatingRowColors(True)
        self.files.setMinimumHeight(160)
        self.files.verticalHeader().setVisible(False)
        data_panel.addWidget(self.files, 3)

        # Advanced local FITS coverage parameters are available on demand;
        # they should not permanently hide the file table's rows.
        self.qa_toggle = QPushButton("Coverage QA settings")
        self.qa_toggle.setCheckable(True)
        self.qa_toggle.setChecked(False)
        self.qa_toggle.setToolTip(
            "Show circular source and sky-ring radii for local FITS diagnostics")
        data_panel.addWidget(self.qa_toggle)
        self.qa_widget = QWidget()
        qa_layout = QVBoxLayout(self.qa_widget)
        qa_layout.setContentsMargins(0, 0, 0, 0)
        qa_layout.setSpacing(4)
        self.target_radius = QDoubleSpinBox()
        self.target_radius.setRange(1, 1000)
        self.target_radius.setDecimals(1)
        self.target_radius.setValue(60.)
        self.target_radius.setToolTip(
            "Circular object screening region; not a fitted isophote")
        self.sky_inner = QDoubleSpinBox()
        self.sky_inner.setRange(1, 1100)
        self.sky_inner.setDecimals(1)
        self.sky_inner.setValue(90.)
        self.sky_inner.setToolTip("Inner radius of diagnostic sky ring")
        self.sky_outer = QDoubleSpinBox()
        self.sky_outer.setRange(1, 1200)
        self.sky_outer.setDecimals(1)
        self.sky_outer.setValue(180.)
        self.sky_outer.setToolTip("Outer radius of diagnostic sky ring")
        for label, control in (
                ("Object radius (arcsec)", self.target_radius),
                ("Sky inner (arcsec)", self.sky_inner),
                ("Sky outer (arcsec)", self.sky_outer)):
            line = QHBoxLayout()
            line.addWidget(QLabel(label))
            line.addWidget(control)
            qa_layout.addLayout(line)
        self.qa_btn = QPushButton("Inspect local FITS")
        self.qa_btn.setToolTip(
            "Analyse downloaded IMAGE, INVVAR, NEXP and MASKBITS")
        self.qa_btn.clicked.connect(self.inspect_coverage)
        qa_layout.addWidget(self.qa_btn)
        data_panel.addWidget(self.qa_widget)
        self.qa_toggle.toggled.connect(self.qa_widget.setVisible)
        self.qa_widget.setVisible(False)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save manifest")
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        self.download_btn = QPushButton("Download selected")
        self.download_btn.clicked.connect(self.download)
        bottom.addWidget(self.download_btn)
        # Footer stays pinned even while product rows scroll.
        self.data_drawer = QDockWidget("Archive files · coverage QA", self)
        self.data_drawer.setObjectName("science_data_drawer")
        self.data_drawer.setAllowedAreas(Qt.RightDockWidgetArea)
        self.data_drawer.setFeatures(
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        # A wide file table must scroll within the drawer, never force the
        # whole top-level application wider than the user's screen.
        self.data_scroll = QScrollArea()
        self.data_scroll.setWidgetResizable(True)
        self.data_scroll.setObjectName("science_data_scroll")
        self.data_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.data_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.data_scroll.setMinimumWidth(150)
        self.data_scroll.setWidget(data_container)
        drawer_body = QWidget()
        drawer_layout = QVBoxLayout(drawer_body)
        drawer_layout.setContentsMargins(3, 2, 3, 2)
        drawer_layout.setSpacing(4)
        drawer_layout.addWidget(self.data_scroll, 1)
        drawer_layout.addLayout(bottom, 0)
        self.data_drawer.setWidget(drawer_body)
        self.addDockWidget(Qt.RightDockWidgetArea, self.data_drawer)
        self.data_drawer.visibilityChanged.connect(self._drawer_visibility_changed)
        self.data_drawer.hide()

        # Progress remains visible even when the data drawer is closed.
        self.bar = QProgressBar()
        self.bar.setValue(0)
        panel.addWidget(self.bar)
        self.message = QLabel("Ready")
        self.message.setWordWrap(True)
        panel.addWidget(self.message)

        self.sky = QWebEngineView()
        self.sky.setAccessibleName("Official Legacy Survey Viewer remote preview")
        self.sky.setPage(_DiagnosticWebPage(self.sky))
        self.sky.setZoomFactor(self.web_scale.value())
        self.sky.loadStarted.connect(self._viewer_load_started)
        self.sky.loadFinished.connect(self._viewer_load_finished)
        self.navigation_scroll = QScrollArea()
        self.navigation_scroll.setObjectName("survey_scroll")
        self.navigation_scroll.setWidgetResizable(True)
        self.navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation_scroll.setWidget(left)
        self.navigation_scroll.setMinimumWidth(275)
        self.navigation_scroll.setMaximumWidth(425)
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_layout.setSpacing(4)
        self.preview_label = QLabel(
            "REMOTE PREVIEW · Legacy Survey Viewer · not calibrated FITS input")
        self.preview_label.setAccessibleName("Remote image provenance warning")
        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.sky, 1)
        self.workspace_split = split
        split.addWidget(self.navigation_scroll)
        split.addWidget(preview_container)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        split.setSizes([375, 1085])
        self.setCentralWidget(split)
        self.search_btn.setDefault(True)
        # Navigation to a remote web application is deliberately explicit.
        # RA/Dec spinbox edits must not trigger many remote page reloads.
        self.show_viewer()

    def _toggle_data_drawer(self):
        visible = not self.data_drawer.isVisible()
        self.data_drawer.setVisible(visible)
        if visible:
            self._size_data_drawer()

    def _size_data_drawer(self):
        """Bound the right-side inspector to protect the map's width.

        At laptop widths the navigation contracts so the online sky viewer
        remains the main area. Users can still resize/hide the inspector.
        """
        width = self.width()
        # Fit the inspector FIRST: QMainWindow can otherwise recompute the
        # splitter using its pre-dock width and leave too little room for sky.
        desired = max(280, min(395, round(width * 0.195)))
        self.resizeDocks([self.data_drawer], [desired], Qt.Horizontal)
        if width < 1500:
            navigation = 278
        elif width < 1800:
            navigation = 296
        else:
            navigation = 325
        self.workspace_split.setSizes([navigation, 10000])

    def _drawer_visibility_changed(self, visible):
        self.data_toggle.setText(
            "Hide files and coverage" if visible else "Files and coverage")

    def _apply_web_scale(self, value):
        """Web UI scaling, independent of world-coordinate zoom."""
        self.sky.setZoomFactor(float(value))

    def _field(self):
        # Check the editable text only when a scientific action is requested.
        return SkyField(self.ra.value(), self.dec.value(), self.field_width.value())

    def _validated_field(self):
        """Block network/science actions on malformed pasted coordinates."""
        for widget in (self.ra, self.dec, self.field_width):
            try:
                widget.value()
            except ValueError as exc:
                widget._mark_invalid(str(exc))
                widget.setFocus()
                widget.selectAll()
                self.target_hint.setText(str(exc))
                QMessageBox.warning(self, "Invalid target coordinates", str(exc))
                return None
        try:
            field = self._field()
        except ValueError as exc:
            self.target_hint.setText(str(exc))
            QMessageBox.warning(self, "Invalid target coordinates", str(exc))
            return None
        self.target_hint.setText("ICRS target ready · press Find data or Show target")
        return field

    def _target_edited(self, *_):
        # The existing bricks, if any, describe the previous target field.
        self.target_hint.setText(
            "Target changed · run Find data again to refresh the bricks"
            if self.manifest is not None else
            "Target edited · press Find data or Show target")

    def paste_coordinate_pair(self):
        """Explicit clipboard action; never read clipboard during normal editing."""
        text = QApplication.clipboard().text()
        try:
            ra, dec = parse_coordinate_pair(text)
        except ValueError as exc:
            self.target_hint.setText(str(exc))
            QMessageBox.warning(self, "Cannot paste RA / Dec", str(exc))
            return
        self.ra.setValue(ra)
        self.dec.setValue(dec)
        self.target_hint.setText(
            f"Pasted ICRS: RA {ra:.6f}°, Dec {dec:+.6f}°")

    def _preferred_viewer_region(self, release: str):
        """Return a north/south layer only for a field wholly on one side."""
        field = self._field()
        half = field.width_arcmin / 120.0
        boundary = 32.375
        if field.dec_deg - half > boundary and release in ('dr9', 'dr11'):
            return 'north'
        if field.dec_deg + half < boundary:
            return 'south'
        return None

    def _viewer_link(self, *, field=None, release=None, region=None,
                     overlay_field=None, mark_target=True):
        selected = [name for name, box in self.release_boxes.items()
                    if box.isChecked()]
        release = release or (selected[0] if selected else 'dr9')
        if region is None:
            region = self._preferred_viewer_region(release)
        return viewer_url(field or self._field(), release=release,
                          region=region, view=self.view_mode.currentData(),
                          zoom=13, mark_target=mark_target,
                          show_field=True, overlay_field=overlay_field)

    def show_viewer(self, *_):
        """Navigate Qt directly to the hosted viewer, never as an iframe."""
        if self._validated_field() is None:
            return
        link = self._viewer_link()
        self.message.setText(
            "Opening Legacy Viewer with the ICRS search-area outline. "
            "The colored boundary is a selection preview, not a coverage map.")
        self.sky.load(QUrl(link))

    def preview_brick(self, row: int, _column: int):
        """Preview a brick center; preserve current catalogue/selection state."""
        if row < 0 or row >= len(self.hits):
            return
        hit = self.hits[row]
        mid_ra = (hit.ra1 + ((hit.ra2-hit.ra1) % 360.)/2.) % 360.
        mid_dec = (hit.dec1 + hit.dec2) / 2.
        field_width = self._validated_field()
        if field_width is None:
            return
        field = SkyField(mid_ra, mid_dec, field_width.width_arcmin)
        # Brick preview recenters the map but retains the *original*
        # target-search footprint. Do not move the outline with the brick.
        link = self._viewer_link(
            field=field, release=hit.release, region=hit.region,
            overlay_field=field_width, mark_target=False)
        self.sky.load(QUrl(link))
        self.message.setText(
            f"Previewing brick {hit.name} at RA={mid_ra:.5f}, "
            f"Dec={mid_dec:.5f}; target coordinates unchanged.")

    def open_viewer_in_browser(self):
        """Exact ICRS position and release, independent of QtWebEngine networking."""
        if self._validated_field() is None:
            return
        QDesktopServices.openUrl(QUrl(self._viewer_link()))

    def _viewer_load_started(self):
        self.message.setText("Loading official Legacy Sky Viewer in Qt WebEngine…")

    def _viewer_load_finished(self, success: bool):
        if success:
            self.message.setText(
                "Legacy Viewer page loaded. This does not certify that all "
                "tiles or external scripts have loaded; inspect the image.")
        else:
            self.message.setText(
                "Embedded Legacy Viewer page failed. Use Open in browser "
                "or Viewer status to check the cause.")

    def show_viewer_diagnostics(self):
        """Distinguish HTTP document navigation from tile/script rendering."""
        url = self.sky.url().toString()
        console = list(getattr(self.sky.page(), 'recent_console', []))
        probe = """(function(){
            try{return JSON.stringify({
                page:location.href,
                documentReady:document.readyState,
                title:document.title,
                imageCount:document.images.length,
                tileImages:Array.from(document.images).filter(
                    function(i){return i.naturalWidth>0;}).length,
                scripts:document.scripts.length
            });} catch(e){return JSON.stringify({error:String(e)});}
        })()"""
        def done(payload):
            lines = [f"Qt WebEngine URL: {url}"]
            if isinstance(payload, str):
                try:
                    data = json.loads(payload)
                    lines += [f"{k}: {v}" for k, v in data.items()]
                except ValueError:
                    lines.append("Malformed JS status: "+payload[:300])
            else:
                lines.append("No JavaScript response (possibly page navigation).")
            lines.append("Recent JS console:")
            lines.extend(console[-12:] or ["No console messages captured"])
            QMessageBox.information(self, "Legacy Viewer status", chr(10).join(lines))
        self.sky.page().runJavaScript(probe, done)

    def _start(self, action, finished):
        if self.thread is not None:
            QMessageBox.information(self, "Busy", "Another survey operation is running.")
            return
        self.search_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.verify_btn.setEnabled(False)
        self.qa_btn.setEnabled(False)
        thread = QThread(self)
        worker = TaskWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(finished)
        worker.failed.connect(lambda msg: (
            self.message.setText(msg),
            QMessageBox.warning(self, "Survey operation failed", msg)))
        worker.progress.connect(self._progress)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._idle)
        self.worker = worker
        self.thread = thread
        thread.start()

    def _idle(self):
        self.thread = None
        self.worker = None
        self.search_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.verify_btn.setEnabled(True)
        self.qa_btn.setEnabled(True)

    def _progress(self, item):
        if isinstance(item, tuple):
            done, total, path = item
            self.bar.setMaximum(max(total, 1))
            self.bar.setValue(done)
            self.message.setText(f"{done}/{total}: {path}")

    def search(self):
        field = self._validated_field()
        if field is None:
            return
        releases = tuple(dr for dr, box in self.release_boxes.items() if box.isChecked())
        if not releases:
            QMessageBox.warning(self, "Select a release", "Choose at least one data release.")
            return
        self.manifest = None
        self.bricks.setRowCount(0)
        self.files.setRowCount(0)
        self.message.setText("Loading survey geometry and footprint catalogs…")
        self.show_viewer()

        def work(progress):
            hits = discover_bricks(field, releases)
            return hits, build_manifest(field, hits)

        self._start(work, self._receive_search)

    def _receive_search(self, result):
        hits, manifest = result
        self.hits, self.manifest = hits, manifest
        self.bricks.setRowCount(len(hits))
        for row, h in enumerate(hits):
            medians = "/".join(str(h.median_exposures.get(b) or 0)
                               if h.median_exposures.get(b) is not None else "—"
                               for b in ("g", "r", "i", "z"))
            for col, value in enumerate((h.release.upper(), h.region, h.name, medians)):
                self.bricks.setItem(row, col, QTableWidgetItem(str(value)))
        unique = manifest.get("unique_spatial_bricks", len(hits))
        related = len(manifest.get("related_release_groups", ()))
        selected_releases = {
            name for name, checkbox in self.release_boxes.items()
            if checkbox.isChecked()}
        discovered_releases = {hit.release for hit in hits}
        absent = ", ".join(sorted(selected_releases - discovered_releases))
        note = (
            f"{len(hits)} release records across {unique} sky bricks. "
            f"{related} northern DR9/DR11 input-related groups. "
            "The reductions are NOT presumed identical. "
            "NEXP values are brick medians, not pixel coverage.")
        if absent:
            note += f" No matching bricks for selected: {absent.upper()}."
        self.summary.setText(note)
        self.summary.setToolTip(
            "Different data releases may reuse exposures but contain distinct "
            "coadd processing. Do not treat them as independent observations.")
        self._fill_brick_filters()
        self._fill_files()
        self.data_drawer.show()
        self._size_data_drawer()
        self.message.setText(f"{len(manifest['products'])} candidate files")

    def _fill_brick_filters(self):
        previous = self.brick_filter.currentText()
        self.brick_filter.blockSignals(True)
        self.brick_filter.clear()
        self.brick_filter.addItem("All bricks")
        for brick in sorted({hit.name for hit in self.hits}):
            self.brick_filter.addItem(brick)
        index = self.brick_filter.findText(previous)
        self.brick_filter.setCurrentIndex(max(0, index))
        self.brick_filter.blockSignals(False)

    def _fill_files(self, *, checked_paths=None):
        """Compact row identity, full provenance on hover, persistent selection."""
        entries = self.manifest["products"] if self.manifest else []
        self.files.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            check = QCheckBox()
            check.setChecked(
                checked_paths is None or entry["relative_path"] in checked_paths)
            check.toggled.connect(self._update_selection_summary)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(1, 0, 1, 0)
            layout.addWidget(check, alignment=Qt.AlignCenter)
            self.files.setCellWidget(row, 0, holder)
            suffix = f"{entry['kind']}-{entry['band']}" if entry["band"] else entry["kind"]
            name = f"{entry['release'].upper()} {entry['brick']} · {suffix}"
            detail = entry["relative_path"] + chr(10) + entry["url"]
            values = (
                (1, name),
                (2, self._short_http_evidence(entry["evidence"])),
                (3, "—"))
            for col, value in values:
                item = QTableWidgetItem(str(value))
                item.setToolTip(detail + chr(10) + f"Status: {entry['evidence']}")
                self.files.setItem(row, col, item)
        self._filter_products()

    @staticmethod
    def _short_http_evidence(evidence):
        return {
            "HTTP_OK": "OK", "HTTP_MISSING": "404",
            "HTTP_ERROR": "ERR", "HTTP_UNVERIFIED": "?", 
            "UNVERIFIED_URL": "?",
        }.get(evidence, "?")

    def _is_visible_product(self, entry):
        return matching_product(
            entry,
            brick=self.brick_filter.currentText(),
            band=self.band_filter.currentText(),
            kind=self.kind_filter.currentText())

    def _filter_products(self, *_):
        if self.manifest is None:
            return
        for row, entry in enumerate(self.manifest["products"]):
            self.files.setRowHidden(row, not self._is_visible_product(entry))
        self._update_selection_summary()

    def _set_visible_selection(self, checked: bool):
        if self.manifest is None:
            return
        for row in range(self.files.rowCount()):
            if not self.files.isRowHidden(row):
                self.files.cellWidget(
                    row, 0).findChild(QCheckBox).setChecked(checked)
        self._update_selection_summary()

    def select_science_quartet(self):
        """Prepare *exactly* four primary science products for a brick and band.

        Explicitly clears the whole manifest selection, not just visible
        rows, so choosing one science bundle cannot accidentally leave 80
        unrelated files selected.
        """
        if self.manifest is None:
            return
        row = self.bricks.currentRow()
        if not (0 <= row < len(self.hits)):
            QMessageBox.information(
                self, "Select a brick",
                "Select one release/region brick row above first.")
            return
        hit = self.hits[row]
        bands = tuple(hit.median_exposures)
        if not bands:
            QMessageBox.warning(self, "No band", "No optical bands listed.")
            return
        default = bands.index("r") if "r" in bands else 0
        band, ok = QInputDialog.getItem(
            self, "Science quartet", "Band:", list(bands), default, False)
        if not ok:
            return
        needed = {
            (hit.release, hit.region, hit.name, "image", band),
            (hit.release, hit.region, hit.name, "invvar", band),
            (hit.release, hit.region, hit.name, "nexp", band),
            (hit.release, hit.region, hit.name, "maskbits", None)}
        available = {
            (e["release"],e["region"],e["brick"],e["kind"],e["band"])
            for e in self.manifest["products"]}
        if not needed.issubset(available):
            QMessageBox.warning(
                self, "Incomplete manifest",
                "Not all four products are in the current download profile.")
            return
        for i, entry in enumerate(self.manifest["products"]):
            key = (entry["release"],entry["region"],entry["brick"],
                   entry["kind"],entry["band"])
            self.files.cellWidget(
                i,0).findChild(QCheckBox).setChecked(key in needed)
        self._update_selection_summary()
        self.message.setText(
            f"Science quartet selected: {hit.release.upper()} "
            f"{hit.region} {hit.name} {band}-band. "
            "Verify URLs before downloading.")

    def _clear_all_selection(self):
        """Explicitly deselect visible AND hidden products."""
        if self.manifest is None:
            return
        for row in range(self.files.rowCount()):
            self.files.cellWidget(
                row, 0).findChild(QCheckBox).setChecked(False)
        self._update_selection_summary()
        self.message.setText(
            "All products deselected, including hidden rows. "
            "Use Select visible or Science quartet to choose download files.")

    def _selected(self):
        if not self.manifest:
            return set()
        return {
            self.manifest["products"][row]["relative_path"]
            for row in range(self.files.rowCount())
            if self.files.cellWidget(row, 0).findChild(QCheckBox).isChecked()
        }

    def _update_selection_summary(self, *_):
        if not self.manifest:
            self.selected_label.setText("0 files selected")
            return
        selected = self._selected()
        report = summarize_selection(self.manifest["products"], selected)
        visible = sum(not self.files.isRowHidden(row)
                      for row in range(self.files.rowCount()))
        sizes = (f"{report.known_mib:.1f} MiB confirmed + unknown"
                 if report.unknown_size_files else
                 f"{report.known_mib:.1f} MiB")
        self.selected_label.setText(
            f"{report.selected_files} selected / {visible} shown · "
            f"{report.verified_files} HTTP OK · {sizes}")
        self.selected_label.setToolTip(
            f"{report.unknown_size_files} selected files have unknown sizes; "
            f"{report.missing_files} known HTTP-missing.")

    def _apply_profile(self, _=None):
        if self.manifest is None:
            return
        previous_selection = self._selected()
        from lsbpipe.surveys.legacy import build_manifest, DEFAULT_PRODUCTS
        kinds = DEFAULT_PRODUCTS if self.profile.currentIndex() == 0 else (
            DEFAULT_PRODUCTS + ("psfsize", "depth", "galdepth", "model"))
        self.manifest = build_manifest(self._field(), self.hits, kinds=kinds)
        self._fill_files(checked_paths=previous_selection)

    def verify(self):
        if not self.manifest:
            return
        selected = self._selected()
        if not selected:
            return
        entries = [x for x in self.manifest["products"] if x["relative_path"] in selected]
        self.message.setText("Checking HTTP HEAD responses…")

        def work(progress):
            results = {}
            for i, entry in enumerate(entries, 1):
                results[entry["relative_path"]] = probe_product(entry)
                progress((i, len(entries), entry["relative_path"]))
            return results

        self._start(work, self._receive_verification)

    def _receive_verification(self, results):
        for row, entry in enumerate(self.manifest["products"]):
            r = results.get(entry["relative_path"])
            if r is None:
                continue
            entry.update(r)
            self.files.item(row, 2).setText(
                self._short_http_evidence(r["evidence"]))
            size = r.get("size_bytes")
            self.files.item(row, 3).setText(
                f"{size / 1048576:.0f}" if size is not None else "—")
            self.files.item(row, 2).setToolTip(
                entry["relative_path"] + chr(10) + f"Status: {r['evidence']}")
            self.files.item(row, 3).setToolTip(
                f"{size} bytes" if size is not None else "Size unknown")
        self._update_selection_summary()
        self.message.setText(
            f"Verified {len(results)} URL responses; check statuses before download.")

    def save(self):
        if self.manifest is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save candidate manifest", "legacy-download.json", "JSON files (*.json)")
        if path:
            save_manifest(self.manifest, Path(path))

    def download(self):
        if self.manifest is None:
            return
        selected = self._selected()
        if not selected:
            QMessageBox.warning(self, "Nothing selected", "Select at least one product.")
            return
        report = summarize_selection(self.manifest["products"], selected)
        amount = (f"Known size: {report.known_mib:.1f} MiB" +
                  (f" + {report.unknown_size_files} unknown-sized files."
                   if report.unknown_size_files else "."))
        text = (
            f"Download {report.selected_files} science products?\n"
            f"{report.verified_files} HTTP OK, "
            f"{report.missing_files} confirmed missing, "
            f"{report.unknown_files} not verified.\n"
            f"{amount}\n\n"
            "These are original FITS products and can occupy significant disk space.")
        if QMessageBox.question(
                self, "Confirm science download", text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Choose the science data directory", str(Path.home()))
        if not directory:
            return
        manifest = json.loads(json.dumps(self.manifest))
        self.bar.setValue(0)
        self.message.setText("Downloading original archive FITS files…")

        def work(progress):
            return download_manifest(
                manifest, Path(directory), product_paths=selected,
                progress=lambda a,b,c: progress((a,b,c)))

        self._start(work, self._receive_download)

    def inspect_coverage(self):
        """Diagnose a previously downloaded image, IVAR, NEXP and MASKBITS bundle."""
        if not self.hits:
            QMessageBox.information(self, "No bricks", "Search a survey field first.")
            return
        row = self.bricks.currentRow()
        if row < 0 or row >= len(self.hits):
            QMessageBox.information(
                self, "Select a brick",
                "Select exactly one brick row above, then inspect its local FITS.")
            return
        hit = self.hits[row]
        # A median NEXP of zero does NOT prove no exposure at this position.
        # Offer every band described by the release summary for inspection.
        bands = tuple(hit.median_exposures)
        if not bands:
            QMessageBox.warning(
                self, "No band in summary", "Release summary has no band columns.")
            return
        band, accepted = QInputDialog.getItem(
            self, "Inspect local coverage", "Optical band:", list(bands),
            0, False)
        if not accepted:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Select root of downloaded Legacy files", str(Path.home()))
        if not path:
            return
        target, inner, outer = (self.target_radius.value(),
                                self.sky_inner.value(), self.sky_outer.value())
        if not (0 < target <= inner < outer <= 1200):
            QMessageBox.warning(
                self, "Invalid sky radii",
                "Require 0 < object radius <= sky-inner < sky-outer <=1200 arcsec")
            return
        field = self._validated_field()
        if field is None:
            return
        destination = Path(path) / "coverage-reports" / (
            f"{hit.release}_{hit.region}_{hit.name}_{band}.json")
        self.message.setText("Computing source/sky pixel coverage from local FITS…")

        def job(progress):
            from lsbpipe.surveys.coverage import inspect_local_brick, save_coverage
            result = inspect_local_brick(
                Path(path), release=hit.release, region=hit.region,
                brick=hit.name, band=band,
                ra_deg=field.ra_deg, dec_deg=field.dec_deg,
                target_radius_arcsec=target,
                sky_inner_arcsec=inner, sky_outer_arcsec=outer)
            save_coverage(result, destination)
            return result, destination

        self._start(job, self._coverage_ready)

    def _coverage_ready(self, outcome):
        report, destination = outcome
        lines = [
            f"{report.band}-band, status: {report.status}",
            "Note: local circular screening, NOT a photometry acceptance decision."]
        for region in report.regions:
            if region.usable_fraction is None:
                percent = "no sampled pixels"
            else:
                percent = f"{100.*region.usable_fraction:.2f}% usable"
            lines.append(
                f"{region.label}: {percent}; "
                f"field-fraction≈{region.estimated_footprint_fraction:.3f}; "
                f"NPIX={region.pixel_centers_sampled}")
        lines.extend(["", "Warnings:"] + list(report.warnings))
        lines.extend(["", "Saved diagnostic:", str(destination)])
        self.message.setText(
            "Coverage report created: " + str(destination))
        QMessageBox.information(
            self, "Legacy FITS coverage — diagnostic", chr(10).join(lines))

    def _receive_download(self, report):
        successes = sum(x["state"] != "ERROR" for x in report["results"])
        failures = len(report["results"]) - successes
        self.message.setText(
            f"Download finished: {successes} files saved/reused, {failures} errors. "
            "See download-report.json.")
        if failures:
            QMessageBox.warning(self, "Incomplete download",
                f"{failures} files failed. Inspect download-report.json.")

    def closeEvent(self, event):
        if self.thread is not None:
            QMessageBox.information(self, "Download in progress",
                                    "Complete the active operation before closing.")
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    app = QApplication.instance() or QApplication([])
    window = SurveyExplorer()
    window.show()
    return app.exec()
