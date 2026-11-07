"""PySide6 survey explorer: official Legacy Sky Viewer and archive downloads.

Requires the optional 'gui' extra, a working QtWebEngine and network access
for the hosted Legacy Sky Viewer and the Legacy archive. No GUI dependency is
imported by the scientific numerical core.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal, Slot, QUrl, QTimer, QEvent
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QLineEdit,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QInputDialog,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QDockWidget, QScrollArea, QSizePolicy,
    QTabWidget)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

from lsbpipe.surveys.legacy import (
    SkyField, build_manifest, discover_bricks, save_manifest)
from lsbpipe.surveys.transfer import probe_product, download_manifest
from lsbpipe.surveys.viewer import viewer_url, suggested_zoom
from lsbpipe.surveys.selection import matching_product, summarize_selection
from lsbpipe.surveys.local_project import (
    open_local_project, save_local_project, last_project_directory)
from lsbpipe.surveys.brick_overlay import brick_shapes, installation_script, highlight_script
from lsbpipe.gui.target_input import (
    parse_ra_deg, parse_dec_deg, parse_width_arcmin, parse_coordinate_pair)
from lsbpipe.gui.qa_workbench import ScienceQAControls
from lsbpipe.gui.fits_explorer import FITSExplorer
from lsbpipe.gui.dataset_manager import DatasetManager
from lsbpipe.gui.photometry_workspace import PhotometryWorkspace
from lsbpipe.gui.four_layer_comparison import FourLayerComparison
from lsbpipe.gui.multiband_comparison import MultibandComparison, load_multiband_originals
from lsbpipe.gui.fits_raster import prepare_fits_explorer
from lsbpipe.gui.science_workflows import run_science_qa as run_science_qa_backend
from lsbpipe.gui.qa_records import ScienceContext
from lsbpipe.gui.task_execution import (
    TaskWorker, TaskDispatch, TaskToken, WorkspaceTaskController)
from lsbpipe.gui.target_controller import TargetController, TargetTextField
from lsbpipe.gui.survey_data_controller import SurveyDataController
from lsbpipe.gui.science_context_controller import ScienceContextController
from lsbpipe.gui.workflow_ux import workflow_cue


class _DiagnosticWebPage(QWebEnginePage):
    """Collect WebEngine JavaScript console output for on-device diagnostics."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recent_console = []

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        self.recent_console.append(
            f"{str(level)}: {message} ({source_id}:{line_number})")
        del self.recent_console[:-20]


class _NoWebEngineSky(QLabel):
    """Diagnostic sky tab without a WebEngine view, process or GPU surface.

    This isolates embedded Chromium from the rest of Qt Widgets. All
    network-backed survey catalog discovery and local FITS workflows remain
    available, and the official Viewer can still open externally.
    """
    def __init__(self):
        super().__init__(
            "Embedded Legacy Viewer disabled for rendering diagnosis.\n"
            "Find data, inspect candidate FITS, use Data Manager, or "
            "open Legacy Viewer in your browser via More target options.")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setObjectName("remote_webengine_isolation_placeholder")

    def load(self, _url):
        # Do not navigate, start Chromium or falsely report tiles loaded.
        pass

    def url(self):
        return QUrl()

    def page(self):
        return self

    def runJavaScript(self, _script, callback=None):
        if callback is not None:
            callback(None)


class SurveyExplorer(QMainWindow):
    """Object-bound Legacy FITS workspace; may also run in legacy standalone mode."""
    returnToProjects = Signal()

    def __init__(self, *, restore_session=True, launch_remote=True):
        super().__init__()
        self.setWindowTitle("DESIDERA — Legacy Survey Explorer (experimental)")
        self.resize(1460, 880)
        self.hits = []
        self.manifest = None
        self.worker = None
        self.thread = None
        self._task_dispatch = None
        self._active_task_token = None
        self._operation_journal_path = None
        self._closing_task_ui = False
        self._task_serial = 0
        self._brick_overlay_features = []
        self._brick_overlay_keys = []
        self._qa_pixmap = None
        self._comparison_requested = False
        self._qa_generation = 0
        self._last_download_root = None
        self._local_project = None
        self._object_project = None
        self._returning_to_hub = False
        self._restore_session = restore_session
        self._launch_remote = launch_remote
        self._restoring_local_project = False
        self._manual_target_edited = False
        self._project_save_timer = QTimer(self)
        self._project_save_timer.setSingleShot(True)
        self._project_save_timer.setInterval(250)
        self._project_save_timer.timeout.connect(self._save_project_state)
        self._task_controller = WorkspaceTaskController(self)
        self._target_controller = TargetController(self)
        self._data_controller = SurveyDataController(self)
        self._science_context_controller = ScienceContextController(self)
        self._build_ui()
        self._refresh_workflow()
        if self._restore_session:
            self._restore_last_project()

    def _build_ui(self):
        # Geometry/legibility rules only; retain OS light/dark palette.
        # The hosted Legacy web page is visually independent of this stylesheet.
        self.setStyleSheet(
            "QPushButton, QComboBox, QDoubleSpinBox { min-height: 27px; } "
            "QPushButton { padding-left: 8px; padding-right: 8px; } "
            "QPushButton[primaryAction='true'] { "
            "background: palette(highlight); "
            "color: palette(highlighted-text); "
            "border: 1px solid palette(highlight); "
            "border-radius: 6px; font-weight: 600; min-height: 31px; } "
            "QPushButton[primaryAction='true']:disabled { "
            "background: palette(button); color: palette(mid); "
            "border: 1px solid palette(mid); } "
            "QPushButton[disclosureAction='true'] { "
            "text-align: left; background: transparent; border: 0px; "
            "padding: 5px 4px; color: palette(text); } "
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
        # Project navigation belongs above the coordinates card, not inside
        # the target parameters and scientific input controls.
        self.back_to_projects = QPushButton("← All galaxy projects")
        self.back_to_projects.setObjectName("workspace_back_to_projects")
        self.back_to_projects.setAccessibleName("Return to galaxy projects")
        self.back_to_projects.clicked.connect(self._go_back_to_projects)
        self.back_to_projects.setVisible(False)
        panel.addWidget(self.back_to_projects)
        self.dataset_manager_btn = QPushButton("Data Manager · local sources")
        self.dataset_manager_btn.setObjectName("workspace_open_dataset_manager")
        self.dataset_manager_btn.setToolTip(
            "Inspect all local observational sources, bricks and filters "
            "before configuring scientific analysis. No network requests.")
        self.dataset_manager_btn.clicked.connect(self._open_dataset_manager)
        self.dataset_manager_btn.setVisible(False)
        panel.addWidget(self.dataset_manager_btn)
        self.photometry_btn = QPushButton("Photometry · PSF workspace")
        self.photometry_btn.setObjectName("workspace_open_photometry")
        self.photometry_btn.setToolTip(
            "Open the separate scientific photometry screen for this galaxy. "
            "No measurements are performed or adopted by navigation.")
        self.photometry_btn.clicked.connect(self._open_photometry)
        self.photometry_btn.setVisible(False)
        panel.addWidget(self.photometry_btn)
        self.workflow_stage = QLabel("01 / 03 · Target")
        self.workflow_stage.setObjectName("workflow_stage")
        self.workflow_stage.setStyleSheet(
            "font-size: 15px; font-weight: 600; padding: 3px 0;")
        self.workflow_hint = QLabel(
            "Enter ICRS coordinates, then find survey data.")
        self.workflow_hint.setObjectName("workflow_next_step")
        self.workflow_hint.setWordWrap(True)
        self.workflow_hint.setStyleSheet(
            "color: palette(text); font-size: 12px;")
        panel.addWidget(self.workflow_stage)
        panel.addWidget(self.workflow_hint)

        target_box = QGroupBox("Coordinates · ICRS")
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

        self.advanced_target = QWidget()
        self.advanced_target.setObjectName("target_advanced_options")
        self.advanced_target_layout = QVBoxLayout(self.advanced_target)
        self.advanced_target_layout.setContentsMargins(2, 4, 2, 4)
        self.advanced_target_layout.setSpacing(6)
        self.paste_target_btn = QPushButton("Paste RA + Dec")
        self.paste_target_btn.setToolTip(
            "Paste a pair from the clipboard, e.g. 154.7709 46.4537 "
            "or 10:19:05 +46:27:13")
        self.paste_target_btn.clicked.connect(self.paste_coordinate_pair)
        self.advanced_target_layout.addWidget(self.paste_target_btn)
        self.target_hint = QLabel("Decimal degrees, decimal comma or sexagesimal")
        self.target_hint.setObjectName("target_validation_hint")
        self.target_hint.setWordWrap(True)
        self.target_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.advanced_target_layout.addWidget(self.target_hint)
        for field in (self.ra, self.dec, self.field_width):
            field.textChanged.connect(self._target_edited)

        target_form.addWidget(QLabel("Data releases · select before Find data"))
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
        for checkbox in self.release_boxes.values():
            checkbox.toggled.connect(self._release_selection_changed)
        actions = QHBoxLayout()
        self.search_btn = QPushButton("Find data")
        self.search_btn.setProperty("primaryAction", True)
        self.search_btn.setToolTip("Discover Legacy archive bricks for this ICRS field")
        self.search_btn.clicked.connect(self.search)
        actions.addWidget(self.search_btn)
        self.center_btn = QPushButton("Show target")
        self.center_btn.clicked.connect(self.show_viewer)
        actions.addWidget(self.center_btn)
        target_form.addLayout(actions)
        secondary_actions = QHBoxLayout()
        self.external_btn = QPushButton("Open in browser")
        self.external_btn.clicked.connect(self.open_viewer_in_browser)
        secondary_actions.addWidget(self.external_btn)
        self.advanced_target_layout.addLayout(secondary_actions)
        self.open_project_btn = QPushButton("Open existing FITS project…")
        self.open_project_btn.setObjectName("open_existing_local_project")
        self.open_project_btn.setToolTip(
            "Restore a downloaded Legacy FITS project directly from disk. "
            "No archive search or network download is required.")
        self.open_project_btn.clicked.connect(self._open_project_dialog)
        self.advanced_target_layout.addWidget(self.open_project_btn)
        self.project_status = QLabel("No local project opened")
        self.project_status.setObjectName("local_project_status")
        self.project_status.setWordWrap(True)
        self.advanced_target_layout.addWidget(self.project_status)
        self.target_options = QPushButton("More target options")
        self.target_options.setProperty("disclosureAction", True)
        self.target_options.setObjectName("target_advanced_toggle")
        self.target_options.setCheckable(True)
        self.target_options.setChecked(False)
        self.target_options.setToolTip(
            "Clipboard input, remote viewer settings "
            "and technical project information")
        target_form.addWidget(self.target_options)
        target_form.addWidget(self.advanced_target)
        self.target_options.toggled.connect(self.advanced_target.setVisible)
        self.advanced_target.setVisible(False)
        panel.addWidget(target_box)

        viewer_controls = QHBoxLayout()
        viewer_controls.addWidget(QLabel("Viewer"))
        self.view_mode = QComboBox()
        self.view_mode.addItem("Image", "image")
        self.view_mode.addItem("Tractor model", "model")
        self.view_mode.addItem("Residual", "resid")
        self.view_mode.currentIndexChanged.connect(self.show_viewer)
        viewer_controls.addWidget(self.view_mode, 2)
        self.advanced_target_layout.addLayout(viewer_controls)
        self.data_toggle = QPushButton("02 · Data files")
        self.data_toggle.setAccessibleName("Show or hide file selection and coverage")
        self.data_toggle.setToolTip(
            "Open the download and local FITS coverage workbench")
        self.data_toggle.clicked.connect(self._toggle_data_drawer)
        panel.addWidget(self.data_toggle)

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
        self.bricks_heading = QLabel("Survey bricks")
        self.bricks_heading.setObjectName("survey_brick_heading")
        panel.addWidget(self.bricks_heading)
        panel.addWidget(self.bricks, 1)
        self.bricks.cellDoubleClicked.connect(self.preview_brick)
        self.bricks.itemSelectionChanged.connect(self._selected_brick_changed)
        self.bricks.setMouseTracking(True)
        self.bricks.viewport().installEventFilter(self)
        self.bricks.cellEntered.connect(self._hover_brick_row)
        self.bricks_help = QLabel("Select a brick to work with its files.")
        self.bricks_help.setStyleSheet("color: palette(text);")
        self.bricks_help.setWordWrap(True)
        panel.addWidget(self.bricks_help)
        self.bricks_heading.setVisible(False)
        self.bricks.setVisible(False)
        self.bricks_help.setVisible(False)
        panel.addStretch(1)

        # File selection and coverage are a separate, collapsible task surface.
        # They no longer set a 900+ px minimum width on the survey navigation.
        data_container = QWidget()
        data_panel = QVBoxLayout(data_container)
        data_panel.setContentsMargins(10, 8, 10, 8)
        data_panel.setSpacing(6)

        # A vertical inspector should not behave like a wide spreadsheet.
        data_panel.addWidget(QLabel("Files from the Legacy Surveys"))
        self.advanced_files = QWidget()
        self.advanced_files.setObjectName("archive_advanced_options")
        advanced_files_layout = QVBoxLayout(self.advanced_files)
        advanced_files_layout.setContentsMargins(0, 4, 0, 4)
        advanced_files_layout.setSpacing(6)
        self.profile = QComboBox()
        self.profile.addItems([
            "Standard: image, invvar, maskbits, nexp, Tractor",
            "Extended: +psfsize, depth, galdepth, model"])
        self.profile.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.profile.setMinimumContentsLength(13)
        self.profile.currentIndexChanged.connect(self._apply_profile)
        advanced_files_layout.addWidget(self.profile)
        self.verify_btn = QPushButton("Check selected URLs")
        self.verify_btn.clicked.connect(self.verify)
        advanced_files_layout.addWidget(self.verify_btn)

        filters = QHBoxLayout()
        self.brick_filter = QComboBox()
        self.brick_filter.addItem("All bricks")
        self.brick_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.brick_filter, 2)
        self.band_filter = QComboBox()
        self.band_filter.addItems(["All bands", "g", "r", "i", "z"])
        self.band_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.band_filter, 1)
        advanced_files_layout.addLayout(filters)
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(
            ["All products", "image", "invvar", "maskbits", "nexp",
             "tractor", "psfsize", "depth", "galdepth", "model"])
        self.kind_filter.currentIndexChanged.connect(self._filter_products)
        advanced_files_layout.addWidget(self.kind_filter)

        self.selected_label = QLabel("0 files selected")
        self.selected_label.setWordWrap(True)
        data_panel.addWidget(self.selected_label)
        self.science_btn = QPushButton("Select science quartet (4 FITS)")
        self.science_btn.setEnabled(False)
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
        advanced_files_layout.addLayout(selection_actions)
        self.clear_all_btn = QPushButton("Clear all (including hidden)")
        self.clear_all_btn.setToolTip(
            "Deselect every file, including entries hidden by the current filters")
        self.clear_all_btn.clicked.connect(self._clear_all_selection)
        advanced_files_layout.addWidget(self.clear_all_btn)

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
        # Candidate files are primary workflow content, not an advanced option.
        # Display immediately after Find data, retaining unverified URL status.
        data_panel.addWidget(self.files, 3)
        self.files.hide()

        # Advanced local FITS coverage parameters are available on demand;
        # they should not permanently hide the file table's rows.
        self.qa_toggle = QPushButton("Coverage QA settings")
        self.qa_toggle.setCheckable(True)
        self.qa_toggle.setChecked(False)
        self.qa_toggle.setToolTip(
            "Show circular source and sky-ring radii for local FITS diagnostics")
        advanced_files_layout.addWidget(self.qa_toggle)
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
        advanced_files_layout.addWidget(self.qa_widget)
        self.files_options = QPushButton("Advanced file filters and checks")
        self.files_options.setProperty("disclosureAction", True)
        self.files_options.setObjectName("archive_advanced_toggle")
        self.files_options.setCheckable(True)
        self.files_options.setChecked(False)
        self.files_options.setToolTip(
            "The original FITS candidates above remain visible. Show "
            "optional filters, HTTP checks, QA parameters and manifest export.")
        data_panel.addWidget(self.files_options)
        data_panel.addWidget(self.advanced_files)
        self.files_options.toggled.connect(self.advanced_files.setVisible)
        self.advanced_files.setVisible(False)
        # Anchor the short collapsed workbench at top of the inspector.
        data_panel.addStretch(1)
        self.qa_toggle.toggled.connect(self.qa_widget.setVisible)
        self.qa_widget.setVisible(False)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save manifest")
        self.save_btn.clicked.connect(self.save)
        advanced_files_layout.addWidget(self.save_btn)
        self.download_btn = QPushButton("Download selected")
        self.download_btn.setProperty("primaryAction", True)
        self.download_btn.setEnabled(False)
        self.download_btn.setToolTip(
            "Select a science quartet or individual files first. Nothing "
            "is selected for download automatically.")
        self.download_btn.clicked.connect(self.download)
        bottom.addWidget(self.download_btn)
        # Footer stays pinned even while product rows scroll.
        self.data_drawer = QDockWidget("Archive files · coverage QA", self)
        self.data_drawer.setObjectName("science_data_drawer")
        self.data_drawer.setMinimumWidth(278)
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
        # Scientific analysis occupies its own tab, preserving the archive
        # file table and pinned Save/Download actions without squeezing them.
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("science_inspector_tabs")
        self.inspector_tabs.addTab(drawer_body, "Archive files")
        self.science_qa = ScienceQAControls()
        self.science_qa.requested.connect(self.start_science_qa)
        self.science_qa.historyRequested.connect(self._review_science_history)
        self.science_qa.contextChanged.connect(
            self._on_science_context_changed)
        for radius in (self.target_radius, self.sky_inner, self.sky_outer):
            radius.valueChanged.connect(self._on_science_context_changed)
        self.inspector_tabs.addTab(self.science_qa, "Science QA")
        self.data_drawer.setWidget(self.inspector_tabs)
        self.addDockWidget(Qt.RightDockWidgetArea, self.data_drawer)
        self.data_drawer.visibilityChanged.connect(self._drawer_visibility_changed)
        self.data_drawer.hide()

        # Progress remains visible even when the data drawer is closed.
        self.bar = QProgressBar()
        self.bar.setValue(0)
        self.bar.setVisible(False)
        progress_controls = QHBoxLayout()
        progress_controls.addWidget(self.bar, 1)
        self.stop_task_btn = QPushButton("Request stop")
        self.stop_task_btn.setObjectName("survey_request_stop")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.setToolTip(
            "Cooperative stop: tasks check between safe steps. No worker "
            "thread is forcibly terminated; completed receipts are preserved.")
        self.stop_task_btn.clicked.connect(self._request_stop)
        progress_controls.addWidget(self.stop_task_btn)
        self.progress_widget = QWidget()
        self.progress_widget.setObjectName("workflow_task_progress")
        self.progress_widget.setLayout(progress_controls)
        panel.addWidget(self.progress_widget)
        self.progress_widget.setVisible(False)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(text); font-size: 11px;")
        self.message.setMaximumHeight(58)
        panel.addWidget(self.message)

        # A/B isolation for recurrent top-left ghost overlays on Windows.
        # This never changes the scientific data path or the user's FITS.
        self._webengine_isolated = (
            os.environ.get("DESIDERA_DISABLE_WEBENGINE") == "1")
        if self._webengine_isolated:
            self.sky = _NoWebEngineSky()
        else:
            self.sky = QWebEngineView()
            self.sky.setPage(_DiagnosticWebPage(self.sky))
            self.sky.setZoomFactor(0.8)
            self.sky.loadStarted.connect(self._viewer_load_started)
            self.sky.loadFinished.connect(self._viewer_load_finished)
        self.sky.setAccessibleName(
            "Legacy remote sky preview" if not self._webengine_isolated
            else "WebEngine-disabled diagnostic sky placeholder")
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
        self.preview_label = QLabel("Sky preview · not calibrated FITS")
        self.preview_label.setAccessibleName("Remote image provenance warning")
        heading_row = QHBoxLayout()
        heading_row.addWidget(self.preview_label, 1)
        self.band_compare_btn = QPushButton("Compare bands · g/r/i/z")
        self.band_compare_btn.setObjectName("fits_open_band_comparison")
        self.band_compare_btn.setToolTip(
            "Compare original IMAGE in all locally available optical bands "
            "for the selected brick. WCS-linked sky navigation; no resampling, "
            "PSF matching or photometric color measurement.")
        self.band_compare_btn.clicked.connect(self._toggle_multiband)
        heading_row.addWidget(self.band_compare_btn)
        self.comparison_btn = QPushButton("Inspect data quality · 4 maps")
        self.comparison_btn.setObjectName("fits_open_four_comparison")
        self.comparison_btn.setToolTip(
            "Interactive 2×2 quality viewer for ONE optical band. "
            "Inspect four complementary diagnostics: "
            "IMAGE (signal), INVVAR (weight), NEXP (exposure count), "
            "MASKBITS (flags). This is not a comparison of filters.")
        self.comparison_btn.clicked.connect(self._toggle_four_comparison)
        heading_row.addWidget(self.comparison_btn)
        preview_layout.addLayout(heading_row)
        self.preview_tabs = QTabWidget()
        self.preview_tabs.setObjectName("scientific_preview_tabs")
        self.preview_tabs.addTab(self.sky, "Remote sky")
        self.fits_explorer = FITSExplorer()
        self.preview_tabs.addTab(self.fits_explorer, "FITS Explorer")
        # The four-layer workspace is revealed only when requested. It is a
        # live interactive viewer, not a QPixmap of a previously saved PNG.
        # Keep optional workspaces owned by the tab host from creation.
        # Before their first addTab(), parenting them to QMainWindow made
        # them *visible* at default geometry (0, 0, 100, 30), so their
        # child buttons painted on top of the project-navigation button.
        # Inactive workspaces must never be visible or top-level windows.
        self.four_layer_comparison = FourLayerComparison(self.preview_tabs)
        self.multiband_comparison = MultibandComparison(self.preview_tabs)
        self.dataset_manager = DatasetManager(self.preview_tabs)
        self.photometry_workspace = PhotometryWorkspace(self.preview_tabs)
        for optional_page in (
                self.four_layer_comparison, self.multiband_comparison,
                self.dataset_manager, self.photometry_workspace):
            optional_page.hide()
        self.dataset_manager.registerRequested.connect(
            self._index_dataset_root)
        self.dataset_manager.externalRequested.connect(
            self._register_external_dataset)
        self.dataset_manager.coverageRequested.connect(
            self._inspect_dataset_footprints)
        self.dataset_manager.pixelQaRequested.connect(
            self._inspect_dataset_pixel_coverage)
        self.dataset_manager.checksumRequested.connect(
            self._hash_dataset_asset)
        self.dataset_manager.ccdCatalogueRequested.connect(
            self._index_brick_ccd_catalog)
        self.dataset_manager.backRequested.connect(
            self._leave_dataset_manager)
        self.photometry_workspace.backRequested.connect(
            self._leave_photometry)
        self.photometry_workspace.sourcesRequested.connect(
            self._open_dataset_manager)
        self.photometry_workspace.psf0RunRequested.connect(
            self._run_project_psf0)
        self.photometry_workspace.psf0SaveRequested.connect(
            self._save_project_psf0)
        self.photometry_workspace.psf0AutoFwhmRequested.connect(
            self._suggest_project_psf0_fwhm)
        self.photometry_workspace.psf0TractorRequested.connect(
            self._acquire_project_psf0_tractor)
        from lsbpipe.gui.psf0_batch_controller import launch as launch_psf0_batch
        self.photometry_workspace.psf0BatchRequested.connect(
            lambda request: launch_psf0_batch(self, request))
        self.preview_tabs.currentChanged.connect(self._preview_tab_changed)
        preview_layout.addWidget(self.preview_tabs, 1)
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
        # Auto-resume does not require contacting the hosted Legacy Viewer.
        if self._launch_remote and last_project_directory() is None:
            self.show_viewer()

    def attach_object_project(self, project):
        """Bind explicit project identity before any remote/viewer operations."""
        from lsbpipe.surveys.object_projects import ObjectProject
        if not isinstance(project, ObjectProject):
            raise TypeError("Workspace requires an object-project record")
        self._object_project = project
        self.back_to_projects.setVisible(True)
        self.dataset_manager_btn.setVisible(True)
        self.photometry_btn.setVisible(True)
        from lsbpipe.surveys.dataset_registry import read_dataset_index
        self.dataset_manager.bind_project(project, read_dataset_index(project.root))
        self.setWindowTitle(
            f"DESIDERA · {project.display_name} · Galaxy Workspace")
        self.open_project_btn.setVisible(False)
        if (project.field is not None and
                project.data_root is not None and project.data_root.is_dir()):
            from lsbpipe.surveys.local_project import _candidate_images
            if any(_candidate_images(project.data_root)):
                # The object passport owns the adopted target center, chosen
                # dataset and apertures. Legacy metadata supplies ONLY the
                # read-only on-disk inventory on subsequent openings.
                if not self._open_local_project_folder(
                        project.data_root, from_object=True):
                    raise ValueError(
                        "Cannot open this project's saved original FITS inventory")
                return
        self._restoring_local_project = True
        try:
            self.hits = []
            self.manifest = None
            self.bricks.setRowCount(0)
            self.files.setRowCount(0)
            self.science_qa.set_selected_hit(None)
            self.science_qa.set_data_root(
                project.data_root if project.data_root is not None
                else project.root / "data")
            for widget, value in (
                (self.target_radius, project.target_radius_arcsec),
                (self.sky_inner, project.sky_inner_arcsec),
                (self.sky_outer, project.sky_outer_arcsec),
            ):
                widget.setValue(value)
            if project.field is None:
                self.ra.clear()
                self.dec.clear()
                self.field_width.setValue(5.)
                self.target_hint.setText(
                    "No ICRS position yet · enter RA and Dec before Find data")
                self.project_status.setText(
                    f"{project.display_name} · draft target · no FITS")
            else:
                self.ra.setValue(project.field.ra_deg)
                self.dec.setValue(project.field.dec_deg)
                self.field_width.setValue(project.field.width_arcmin)
                self.target_hint.setText(
                    "Object project ready · choose Find data to discover FITS")
                self.project_status.setText(
                    f"{project.display_name} · target defined · no FITS")
            # A project with no original FITS should open in the survey
            # Explorer, not an empty local image/Science QA workspace.
            # Do not load a remote URL implicitly on ordinary reopening:
            # a new-project wizard may explicitly request that separately.
            self.inspector_tabs.setCurrentWidget(self.inspector_tabs.widget(0))
            self.preview_tabs.setCurrentWidget(self.sky)
            # No catalog and no local FITS: do not occupy a third of the
            # viewport with an empty advanced archive inspector.
            self.data_drawer.hide()
            self.message.setText(
                f"Project {project.display_name} opened in Survey Explorer. "
                "No survey archive search or FITS download has been performed.")
        finally:
            self._restoring_local_project = False
        self._refresh_workflow()

    def _open_photometry(self):
        """Open an isolated, explicitly non-adopted photometry workbench."""
        project = self._object_project
        if project is None:
            QMessageBox.information(
                self, "Galaxy project required",
                "Open a galaxy project before entering Photometry.")
            return
        if self.thread is not None:
            self.message.setText("Finish the active task before changing workspaces.")
            return
        try:
            self.photometry_workspace.bind_project(project)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Cannot open Photometry", str(exc))
            return
        if self.preview_tabs.indexOf(self.photometry_workspace) < 0:
            self.preview_tabs.addTab(self.photometry_workspace, "Photometry")
        self.preview_tabs.setCurrentWidget(self.photometry_workspace)
        self.data_drawer.hide()

    def _run_project_psf0(self, request):
        """Dispatch genuine Leo P native-band ePSF in the existing GUI thread pool."""
        from dataclasses import replace
        from lsbpipe.photometry.psf_gui_backend import (
            run_gui_psf0, psf0_experiment_recipe,
            commit_psf0_manual_review,
        )

        panel = self.photometry_workspace.psf0_panels.get(
            str(request.get("band")), self.photometry_workspace.psf0_panel)
        if self.thread is not None:
            panel.status.setText("Another workspace task is active. "
                                 "Finish or stop it before starting PSF-0.")
            return
        if self._object_project is None or request.get("project_root") != str(self._object_project.root):
            panel.show_failure("Leo P project identity changed before PSF dispatch")
            return
        try:
            base_config = psf0_experiment_recipe(
                float(request["fwhm_pix"]), str(request["experiment"]))
            excluded = tuple(request.get("excluded_training_star_ids", ()))
            config = replace(
                base_config, excluded_training_star_ids=excluded,
                star_catalog_policy=str(
                    request.get("star_catalog_policy", "dao_diagnostic")))
            manual_confirmed = request.get("manual_review_confirmed") is True
            band = str(request["band"])
            radius = float(request["target_protection_arcsec"])
            root = str(self._object_project.root)
        except (ValueError, TypeError, KeyError) as exc:
            panel.show_failure(f"Invalid PSF-0 settings: {exc}")
            return

        def run(progress):
            payload = run_gui_psf0(
                root, band=band, config=config,
                protection_arcsec=radius, progress=progress)
            if manual_confirmed:
                payload["manual_review_receipt"] = commit_psf0_manual_review(
                    payload, explicitly_confirmed=True)
            return payload

        def finished(payload):
            if payload.get("status") == "PSF0_QUALITY_REJECTED_NOT_ADOPTED":
                panel.show_quality_rejection(payload)
                self.message.setText(
                    f"PSF-0 {band} REJECTED by scientific QA. "
                    "Inspect negative ringing and candidate stars in Photometry.")
            else:
                panel.show_result(payload)
                self.message.setText(
                    f"PSF-0 {band} completed as EXPERIMENTAL_NOT_ADOPTED. "
                    "Review holdout stars and QA before saving a diagnostic.")
            if payload.get("manual_review_receipt"):
                panel.show_review_saved(payload["manual_review_receipt"])

        def failed(message):
            panel.show_failure(message)

        self._start(run, finished, failed, operation_name="psf0-preview")
        if self.thread is not None:
            panel.set_busy(True, "PSF-0 running on saved original FITS. "
                           "No science result will be adopted.")

    def _acquire_project_psf0_tractor(self, request):
        """Explicit official native Gaia/Tractor FITS download in current project."""
        from lsbpipe.photometry.psf_gui_backend import acquire_gui_psf0_tractor
        panel = self.photometry_workspace.psf0_panels.get(
            str(request.get("band")), self.photometry_workspace.psf0_panel)
        if self.thread is not None:
            panel.status.setText("Finish the active task before acquiring Gaia data.")
            return
        if (self._object_project is None or
                request.get("project_root") != str(self._object_project.root)):
            panel.status.setText("Leo P project changed. Reopen Photometry.")
            return
        root = str(self._object_project.root)
        def run(progress):
            return acquire_gui_psf0_tractor(root, progress=progress)
        def finished(payload):
            panel.show_tractor_acquired(payload)
            self.message.setText(
                "Original DR11 Tractor/Gaia catalogue verified in current "
                "Leo P project. PSF has NOT been rebuilt or adopted.")
        def failed(message):
            panel.status.setText(
                "DR11 Tractor/Gaia catalogue unavailable or invalid: "
                + message + ". No automatic DAO fallback was used.")
        self._start(run, finished, failed,
                    operation_name="psf0-native-tractor-gaia")
        if self.thread is not None:
            panel.set_busy(True, "Fetching original DR11 Tractor+Gaia "
                           "source catalogue on explicit user request.")

    def _suggest_project_psf0_fwhm(self, request):
        """Use existing project native WCS and local optional PSFSIZE map."""
        from lsbpipe.photometry.psf_gui_backend import suggest_gui_psf0_fwhm
        panel = self.photometry_workspace.psf0_panels.get(
            str(request.get("band")), self.photometry_workspace.psf0_panel)
        if self.thread is not None:
            panel.status.setText("Workspace task active; finish it before PSFSIZE lookup")
            return
        if (self._object_project is None or
                request.get("project_root") != str(self._object_project.root)):
            panel.status.setText("Leo P project changed. Reopen Photometry.")
            return
        band, root = request["band"], str(self._object_project.root)
        def run(progress):
            return suggest_gui_psf0_fwhm(
                root, band=band, progress=progress)
        def finished(payload):
            panel.show_fwhm_suggestion(payload)
        def failed(message):
            detail = (
                "Local DESI PSFSIZE cannot be verified: " + message
                + ". Initial FWHM guess unchanged; inspect the exact "
                "coadd PSFSIZE map or original Tractor PSFSIZE band "
                "column in the active Leo P project.")
            panel.status.setText(detail)
            panel.prior_source.setText(detail)
        self._start(run, finished, failed, operation_name="psf0-seeing-prior")
        if self.thread is not None:
            panel.set_busy(True, "Checking original FITS WCS and optional "
                           "DESI PSFSIZE seeing map (no download).")

    def _save_project_psf0(self, preview):
        """User requested an immutable derivative; recheck original FITS first."""
        from lsbpipe.photometry.psf_gui_backend import save_gui_psf0

        band = (str(preview.get("review_band")) if preview.get("review_band")
                else str(preview["result"].dataset_id.rsplit("/", 1)[-1])
                if preview.get("result") is not None else "")
        panel = self.photometry_workspace.psf0_panels.get(
            band, self.photometry_workspace.psf0_panel)
        if self.thread is not None:
            panel.status.setText("Another workspace task is active; save was not started.")
            return
        if self._object_project is None or (
                preview.get("project_root") != str(self._object_project.root)):
            panel.status.setText(
                "Project changed since PSF preview. Rerun before diagnostic export.")
            return

        def run(progress):
            return save_gui_psf0(preview, progress=progress)

        def finished(path):
            panel.show_saved(path)
            self.message.setText("Immutable, non-adopted PSF-0 diagnostic saved: " + path)

        def failed(message):
            panel.status.setText(
                "Could not save diagnostic; preview is still experimental.")
            panel.qa.appendPlainText("\nDiagnostic export failed: " + message)

        self._start(run, finished, failed, operation_name="psf0-export")
        if self.thread is not None:
            panel.set_busy(True, "Verifying original FITS SHA-256 before "
                           "explicit diagnostic export.")

    def _leave_photometry(self):
        if self.fits_explorer._dataset is not None:
            self.preview_tabs.setCurrentWidget(self.fits_explorer)
        else:
            self.preview_tabs.setCurrentWidget(self.sky)

    def _open_dataset_manager(self):
        """Navigate to the object's observational inventory, never online."""
        project = self._object_project
        if project is None:
            QMessageBox.information(
                self, "Galaxy project required",
                "Create or open a galaxy project before using Data Manager.")
            return
        from lsbpipe.surveys.dataset_registry import read_dataset_index
        try:
            index = read_dataset_index(project.root)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Cannot read dataset index", str(exc))
            return
        self.dataset_manager.bind_project(project, index)
        if self.preview_tabs.indexOf(self.dataset_manager) < 0:
            self.preview_tabs.addTab(self.dataset_manager, "Data Manager")
        self.preview_tabs.setCurrentWidget(self.dataset_manager)
        self.data_drawer.hide()
        # First encounter with a legacy project performs an offline *index*
        # of its known original FITS tree, not a download or science adoption.
        # Existing source indexes are never rebuilt implicitly.
        if (not index["sources"] and project.data_root is not None
                and project.data_root.is_dir()):
            self._index_dataset_root(str(project.data_root))

    def _leave_dataset_manager(self):
        if self.fits_explorer._dataset is not None:
            self.preview_tabs.setCurrentWidget(self.fits_explorer)
        else:
            self.preview_tabs.setCurrentWidget(self.sky)

    def _index_dataset_root(self, directory: str):
        project = self._object_project
        if project is None:
            return
        if self.thread is not None:
            self.message.setText("Finish the active task before indexing FITS.")
            return
        from lsbpipe.surveys.dataset_registry import (
            import_legacy_fits, read_dataset_index)
        self.dataset_manager.set_busy(True)
        self.dataset_manager.source_summary.setText(
            "Scanning original local Legacy FITS paths · no network or data changes")
        def job(progress):
            progress.checkpoint()
            result = import_legacy_fits(project.root, directory)
            progress.checkpoint()
            return result, read_dataset_index(project.root)
        def finished(payload):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                self.message.setText("Older project inventory result ignored.")
                return
            result, index = payload
            self.dataset_manager.set_index(index)
            self.message.setText(
                f"Indexed {result['assets_total']} local FITS file entries "
                f"from {result['sources_total']} source groups. "
                "Originals read-only; FITS/WCS and photometric quality unverified.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.message.setText("Dataset indexing failed: " + message)
                self.dataset_manager.source_summary.setText(
                    "Index failed; original FITS remain untouched: " + message)
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_index")

    def _register_external_dataset(self, path: str, provider: str, band: str):
        project = self._object_project
        if project is None or self.thread is not None:
            return
        from lsbpipe.surveys.dataset_registry import (
            register_external_fits, read_dataset_index)
        self.dataset_manager.set_busy(True)
        self.dataset_manager.source_summary.setText(
            "Registering external original FITS · calibration unassessed")
        def job(progress):
            progress.checkpoint()
            receipt = register_external_fits(
                project.root, path, provider=provider, band=band)
            progress.checkpoint()
            return receipt, read_dataset_index(project.root)
        def finished(payload):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                return
            _, index = payload
            self.dataset_manager.set_index(index)
            self.message.setText(
                f"External {provider}/{band} FITS indexed in place · "
                "native units, PSF and calibration remain unverified.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.dataset_manager.source_summary.setText(
                    "External FITS not indexed: " + message)
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_external_import")

    def _inspect_dataset_footprints(self):
        project = self._object_project
        if project is None or self.thread is not None:
            return
        from lsbpipe.surveys.dataset_coverage import survey_coverage_footprints
        self.dataset_manager.set_busy(True)
        self.dataset_manager.map_status.setText(
            "Reading original FITS WCS headers · geometry only")
        def job(progress):
            return survey_coverage_footprints(
                project.root, checkpoint=progress.checkpoint)
        def finished(result):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                return
            self.dataset_manager.set_coverage(result)
            self.message.setText(
                f"Coverage: {len(result['footprints'])} header footprints, "
                f"{len(result['rejected'])} failed headers; no quality adoption.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.dataset_manager.map_status.setText(
                    "Cannot inspect original FITS geometry: " + message)
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_wcs_footprints")

    def _inspect_dataset_pixel_coverage(self):
        project = self._object_project
        if project is None or self.thread is not None:
            return
        from lsbpipe.surveys.dataset_pixel_qa import inspect_pixel_coverage
        self.dataset_manager.set_busy(True)
        self.dataset_manager.pixel_qa_status.setText(
            "Validating original FITS/WCS and counting target/sky pixels…")
        def job(progress):
            return inspect_pixel_coverage(
                project.root, checkpoint_fn=progress.checkpoint)
        def finished(report):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                return
            self.dataset_manager.set_pixel_qa(report)
            self.message.setText(
                f"Pixel coverage diagnostic: {len(report['rows'])} independent "
                "datasets; no science/photometric adoption.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.dataset_manager.pixel_qa_status.setText(
                    "Pixel coverage diagnostic failed: " + message)
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_pixel_coverage")

    def _hash_dataset_asset(self, asset_id: str):
        project = self._object_project
        if project is None or self.thread is not None:
            return
        from lsbpipe.surveys.dataset_registry import (
            verify_asset_checksum, read_dataset_index)
        self.dataset_manager.set_busy(True)
        self.dataset_manager.details.setText(
            "Calculating original-file SHA-256 · no FITS modification")
        def job(progress):
            progress.checkpoint()
            receipt = verify_asset_checksum(project.root, asset_id)
            progress.checkpoint()
            return receipt, read_dataset_index(project.root)
        def finished(payload):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                return
            receipt, index = payload
            self.dataset_manager.set_index(index)
            self.message.setText(
                f"SHA-256 checked for {receipt['asset_id']}; "
                "this is not FITS/photometric science validation.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.dataset_manager.details.setText(
                    "File SHA-256 verification failed: " + message)
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_file_checksum")

    def _index_brick_ccd_catalog(self, asset_id: str, catalogue_path: str):
        """Import verified local catalogue assertions without touching FITS."""
        project = self._object_project
        if project is None or self.thread is not None:
            return
        from lsbpipe.surveys.legacy_ccd_catalog import import_brick_ccd_catalog
        from lsbpipe.surveys.dataset_registry import read_dataset_index
        self.dataset_manager.set_busy(True)
        self.dataset_manager.details.setText(
            "Reading local Legacy brick-CCDs FITS and verifying provenance…")
        def job(progress):
            progress.checkpoint()
            result = import_brick_ccd_catalog(
                project.root, coadd_asset_id=asset_id,
                catalogue_file=catalogue_path)
            progress.checkpoint()
            return result, read_dataset_index(project.root)
        def finished(payload):
            self.dataset_manager.set_busy(False)
            if self._object_project is not project:
                self.message.setText("CCD import for previous project ignored.")
                return
            receipt, index = payload
            self.dataset_manager.set_index(index)
            self.message.setText(
                f"Indexed {receipt['selected_ccd_count']} catalogue CCD rows "
                "for selected original coadd; shared-exposure evidence is "
                "catalogue-asserted, NOT science-certified.")
        def failed(message):
            self.dataset_manager.set_busy(False)
            if self._object_project is project:
                self.dataset_manager.details.setText(
                    "CCD catalogue import refused: " + message)
                self.message.setText(
                    "CCD metadata rejected; existing FITS and lineage retained.")
        self._start(job, finished, on_error=failed,
                    operation_name="dataset_ccd_catalog")

    def _go_back_to_projects(self):
        if self.thread is not None:
            QMessageBox.information(
                self, "Operation running",
                "Finish the active task before leaving this galaxy project.")
            return
        self._save_project_state()
        self.returnToProjects.emit()

    def _save_object_project_context(self):
        project = self._object_project
        if project is None or self._restoring_local_project:
            return
        from lsbpipe.surveys.object_projects import save_object_project
        try:
            # Resolve the entire new state before touching the manifest.
            # Invalid text drafts never persist half an ICRS position.
            new_field = (
                None if not self.ra.text().strip() and not self.dec.text().strip()
                else self._field())
            row = self.bricks.currentRow()
            selected = project.selected_dataset
            if (self._brick_result_matches_target()
                    and self.bricks.selectedItems()
                    and 0 <= row < len(self.hits)):
                hit = self.hits[row]
                selected = (hit.release, hit.region, hit.name,
                            self.science_qa.current_band())
            project.field = new_field
            project.selected_dataset = selected
            project.target_radius_arcsec = self.target_radius.value()
            project.sky_inner_arcsec = self.sky_inner.value()
            project.sky_outer_arcsec = self.sky_outer.value()
            if self._local_project is not None:
                project.data_root = self._local_project.data_root
                if self._local_project.last_science:
                    project.last_science = self._local_project.last_science
            save_object_project(project)
        except (ValueError, OSError) as exc:
            self.project_status.setText(
                f"Project remains open; saving its context failed: {exc}")

    def _open_project_dialog(self):
        initial = (last_project_directory() or
                   Path(self.science_qa.directory.text().strip() or Path.home()))
        directory = QFileDialog.getExistingDirectory(
            self, "Open existing Legacy FITS project", str(initial))
        if directory:
            self._open_local_project_folder(directory)

    def _open_local_project_folder(self, directory, *, startup=False,
                                   from_object=False):
        """Read-only original FITS discovery, with object passport authority."""
        try:
            obj = self._object_project
            fallback = (self._field() if self._manual_target_edited
                        and not startup and not from_object else None)
            if from_object and (obj is None or obj.field is None):
                raise ValueError("Object project requires an accepted ICRS position")
            project = open_local_project(
                directory, fallback_field=fallback,
                authoritative_field=obj.field if from_object else None,
                authoritative_selected=obj.selected_dataset if from_object
                else None)
            if from_object:
                project.target_radius_arcsec = obj.target_radius_arcsec
                project.sky_inner_arcsec = obj.sky_inner_arcsec
                project.sky_outer_arcsec = obj.sky_outer_arcsec
                project.last_science = obj.last_science
        except (OSError, ValueError, KeyError) as exc:
            self.project_status.setText(f"Cannot open local project: {exc}")
            if not startup:
                QMessageBox.warning(self, "Cannot open local FITS project", str(exc))
            return False
        self._install_local_project(project)
        if self._object_project is None:
            # Legacy standalone mode continues its established session
            # writer. In object-project mode the old 0.1 file is immutable.
            try:
                save_local_project(project)
            except (OSError, ValueError) as exc:
                self.project_status.setText(
                    f"Local files opened, but session could not be saved: {exc}")
        else:
            self._queue_project_save()
        return True

    def _restore_last_project(self):
        root = last_project_directory()
        if root is not None and self._open_local_project_folder(root, startup=True):
            return
        if root is not None:
            self.message.setText(
                "Previously selected project not found or invalid. "
                "Choose Open existing FITS project to recover it.")
            self.show_viewer()

    def _install_local_project(self, project):
        """Rebuild the existing project and selected QA context from disk."""
        from lsbpipe.surveys.legacy import build_manifest
        self._project_save_timer.stop()
        self._restoring_local_project = True
        try:
            self._local_project = project
            self._manual_target_edited = False
            for widget, value in (
                (self.ra, project.field.ra_deg),
                (self.dec, project.field.dec_deg),
                (self.field_width, project.field.width_arcmin),
            ):
                widget.setValue(value)
            for release, checkbox in self.release_boxes.items():
                checkbox.setChecked(
                    any(hit.release == release for hit in project.hits))
            self.hits = project.hits
            self.manifest = build_manifest(project.field, project.hits)
            # Local filenames do not contain official brick geometry or NEXP.
            self._brick_overlay_features, self._brick_overlay_keys = [], []
            self.bricks.blockSignals(True)
            self.bricks.setRowCount(len(self.hits))
            for row, hit in enumerate(self.hits):
                for col, value in enumerate((
                    hit.release.upper(), hit.region, hit.name, "—/—/—/—"
                )):
                    self.bricks.setItem(row, col, QTableWidgetItem(value))
            self.bricks.clearSelection()
            self.bricks.blockSignals(False)
            self._fill_brick_filters()
            self._fill_files(checked_paths=project.on_disk)
            for widget, value in (
                (self.target_radius, project.target_radius_arcsec),
                (self.sky_inner, project.sky_inner_arcsec),
                (self.sky_outer, project.sky_outer_arcsec),
            ):
                widget.setValue(value)
            self.science_qa.set_data_root(project.data_root)
            if any(hit.region == "north" for hit in project.hits):
                self.science_qa.north_directory.setText(str(project.data_root))
            if any(hit.region == "south" for hit in project.hits):
                self.science_qa.south_directory.setText(str(project.data_root))
            chosen = project.selected
            row = next(
                (i for i, hit in enumerate(self.hits) if chosen and
                 (hit.release, hit.region, hit.name) == chosen[:3]), 0)
            unavailable_selection = bool(
                self._object_project is not None
                and self._object_project.selected_dataset is not None
                and chosen != self._object_project.selected_dataset)
            if self.hits and not unavailable_selection:
                self.bricks.selectRow(row)
                if chosen and chosen[3] in self.hits[row].median_exposures:
                    self.science_qa.band.setCurrentText(chosen[3])
                hit = self.hits[row]
                project.selected = (hit.release, hit.region, hit.name,
                                    self.science_qa.current_band())
            elif unavailable_selection:
                # Inventory fallback is informational, not a new scientific
                # selection. Require a deliberate row choice before QA/save.
                self.bricks.clearSelection()
                self.science_qa.set_selected_hit(None)
            self._mark_local_files()
            if unavailable_selection:
                self.project_status.setText(
                    "Saved dataset missing from original FITS inventory. "
                    "Choose a present brick deliberately; the stored "
                    "selection has not been changed.")
            self.target_hint.setText(
                f"Local ICRS target from {project.field_source}; no archive search")
            self.message.setText(
                "Saved dataset unavailable: select a present brick before QA."
                if unavailable_selection else
                f"Restored {project.root.name} from existing original FITS. "
                "Run Science QA without downloading again.")
            self.preview_label.setText(
                "LOCAL PROJECT · offline restored · Remote Viewer not refreshed")
            self.preview_tabs.setCurrentWidget(self.fits_explorer)
            self.inspector_tabs.setCurrentWidget(self.science_qa)
            self.data_drawer.show()
            self._size_data_drawer()
            self._restore_prior_qa(project)
        finally:
            self._restoring_local_project = False
        self._refresh_workflow()

    def _restore_prior_qa(self, project):
        item = project.last_science
        key = project.selected
        if not key or not item or tuple(item.get("provenance", ())) != key:
            return
        path = Path(item.get("path", ""))
        manifest = Path(item.get("manifest_path", ""))
        allowed_roots = [project.data_root.parent / "qa-runs"]
        if self._object_project is not None:
            allowed_roots.append(self._object_project.root / "qa-runs")
        try:
            if not (path.is_file() and manifest.is_file() and
                    not path.is_symlink() and not manifest.is_symlink() and
                    any(manifest.resolve().is_relative_to(q.resolve())
                        for q in allowed_roots) and
                    path.parent.resolve() == manifest.parent.resolve()):
                return
            from lsbpipe.gui.run_contracts import check_run_receipt
            current = self._current_science_context(item.get("action", ""))
            review = check_run_receipt(
                manifest,
                project_id=(self._object_project.project_id
                            if self._object_project is not None else None),
                current_context=(current.to_dict() if current is not None
                                 and item.get("action") != "explore" else None))
            if review["state"] not in ("RECORDED_NOT_REVALIDATED",
                                        "LEGACY_UNVERIFIED"):
                return
            record = json.loads(manifest.read_text(encoding="utf-8"))
            context = record.get("context", {})
            if record.get("schema") != "lsbpipe.gui-science-qa/1":
                return
            if not all(context.get(name) == value for name, value in (
                    ("release", key[0]), ("region", key[1]),
                    ("brick", key[2]), ("band", key[3]))):
                return
            if not all(abs(float(context.get(name, -999)) - value) < 1e-9
                       for name, value in (
                           ("ra_deg", project.field.ra_deg),
                           ("dec_deg", project.field.dec_deg),
                           ("width_arcmin", project.field.width_arcmin))):
                return
        except (OSError, ValueError, TypeError, KeyError):
            return
        self.science_qa.show_result({
            "summary": item.get("summary", "Earlier QA report") +
                "\n\nRESTORED HISTORY: original FITS hashes have not been "
                "revalidated on reopening.",
            "path": str(path), "manifest_path": str(manifest),
            "is_figure": path.suffix.lower() == ".png"})
        self.message.setText(
            "Previous QA report reopened for inspection only; "
            "rerun the diagnostic before scientific use.")

    def _queue_project_save(self):
        if self._restoring_local_project:
            return
        if self._object_project is not None or (
                self._local_project is not None and
                self._brick_result_matches_target()):
            self._project_save_timer.start()

    def _save_project_state(self):
        self._save_object_project_context()
        if self._object_project is not None:
            # Never touch the old project schema after object migration.
            return
        project = self._local_project
        if project is None or self._restoring_local_project:
            return
        if not self._brick_result_matches_target():
            return
        directory = self.science_qa.directory.text().strip()
        if (not directory or Path(directory).resolve() not in (
                project.root.resolve(), project.data_root.resolve())):
            return
        row = self.bricks.currentRow()
        if not (self.bricks.selectedItems() and 0 <= row < len(self.hits)):
            return
        hit = self.hits[row]
        project.selected = (hit.release, hit.region, hit.name,
                            self.science_qa.current_band())
        project.target_radius_arcsec = self.target_radius.value()
        project.sky_inner_arcsec = self.sky_inner.value()
        project.sky_outer_arcsec = self.sky_outer.value()
        try:
            save_local_project(project)
        except (OSError, ValueError) as exc:
            self.project_status.setText(
                f"Science files remain available; session save failed: {exc}")

    def _toggle_data_drawer(self):
        if self.preview_tabs.currentWidget() is self.dataset_manager:
            # The archive inspector belongs to FITS Explorer, not to a
            # project-wide provenance map that needs the viewport width.
            self.data_drawer.hide()
            return
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

    def _refresh_workflow(self):
        """Presentation only; no auto-selection or science acceptance."""
        if not hasattr(self, "workflow_stage"):
            return
        try:
            self._field()
            has_target = True
        except ValueError:
            has_target = False
        row = self.bricks.currentRow()
        selected = bool(
            self.bricks.selectedItems() and 0 <= row < len(self.hits))
        has_catalog = self.manifest is not None and not self._manual_target_edited
        local_available = (
            self._local_project is not None and
            selected and not self._manual_target_edited)
        image_ready = (
            self.fits_explorer._dataset is not None and
            self._qa_context_current(self.fits_explorer._dataset))
        cue = workflow_cue(
            has_target=has_target, has_catalog=has_catalog,
            selected_brick=selected,
            has_local_fits=local_available,
            has_interactive_image=image_ready)
        self.workflow_stage.setText(cue.title)
        self.workflow_hint.setText(cue.instruction)
        self.science_btn.setEnabled(
            selected and bool(has_catalog) and self.thread is None)
        # An empty brick grid is not a meaningful default navigation element.
        has_bricks = bool(self.hits)
        for widget in (self.bricks, self.bricks_heading, self.bricks_help):
            widget.setVisible(has_bricks)
        self.comparison_btn.setVisible(bool(local_available or image_ready))
        manager_active = (
            self.preview_tabs.currentWidget() is self.dataset_manager)
        photometry_active = (
            self.preview_tabs.currentWidget() is self.photometry_workspace)
        auxiliary_active = manager_active or photometry_active
        self.band_compare_btn.setVisible(
            bool(local_available) and not auxiliary_active)
        self.comparison_btn.setVisible(
            bool(local_available or image_ready) and not auxiliary_active)
        self.data_toggle.setEnabled(not auxiliary_active)
        if manager_active:
            self.workflow_stage.setText("02 / 04 · Data sources")
            self.workflow_hint.setText(
                "Inspect indexed observations and WCS footprints. "
                "No primary analysis source has been adopted.")
        if photometry_active:
            self.workflow_stage.setText("03 / 04 · Photometry")
            self.workflow_hint.setText(
                "Experimental PSF-0 preview available for saved Leo P FITS; "
                "no sky, galaxy photometry or science adoption.")
        self.data_toggle.setText(
            "02 · Get original FITS" if has_catalog and not local_available
            else "Data & diagnostics")

    def _field(self):
        return self._target_controller.field()

    def _validated_field(self):
        return self._target_controller.validated_field()

    def _target_edited(self, *_):
        return self._target_controller.edited()

    def paste_coordinate_pair(self):
        return self._target_controller.paste_coordinate_pair()

    def _preferred_viewer_region(self, release: str):
        return self._target_controller.preferred_viewer_region(release)

    def _viewer_link(self, *, field=None, release=None, region=None,
                     overlay_field=None, mark_target=True, include_bricks=False):
        selected = [name for name, box in self.release_boxes.items()
                    if box.isChecked()]
        release = release or (selected[0] if selected else 'dr9')
        if region is None:
            region = self._preferred_viewer_region(release)
        target = overlay_field or self._field()
        selected_row = self.bricks.currentRow()
        active = (self.hits[selected_row]
                  if self.bricks.selectedItems() and
                     0 <= selected_row < len(self.hits) else None)
        return viewer_url(field or self._field(), release=release,
                          region=region, view=self.view_mode.currentData(),
                          zoom=suggested_zoom(target), mark_target=mark_target,
                          show_field=True, overlay_field=target,
                          bricks=(self.hits if include_bricks and
                                  self._brick_result_matches_target() else ()),
                          selected_brick=(active if include_bricks and
                                  self._brick_result_matches_target() else None))

    def show_viewer(self, *_):
        """Navigate Qt directly to the hosted viewer, never as an iframe."""
        field = self._validated_field()
        if field is None:
            return
        self.preview_label.setText(
            "REMOTE PREVIEW · Legacy Viewer · "
            f"search area {field.width_arcmin:g}′ × {field.width_arcmin:g}′ "
            "· not calibrated FITS")
        link = self._viewer_link()
        self.message.setText(
            "Opening Legacy Viewer with the ICRS search-area outline. "
            "The colored boundary is a selection preview, not a coverage map.")
        if self._webengine_isolated:
            self.message.setText(
                "Embedded remote sky disabled for GUI rendering diagnosis. "
                "Archive discovery and local FITS are still available.")
        else:
            self.sky.load(QUrl(link))

    def _brick_result_matches_target(self):
        """Never draw prior search bricks around edited target coordinates."""
        if not self.manifest:
            return False
        try:
            field = self._field()
        except ValueError:
            return False
        saved = self.manifest.get("field", {})
        return all(abs(actual - float(saved.get(name, float("nan")))) < 1e-9
                   for name, actual in (
                       ("ra_deg", field.ra_deg),
                       ("dec_deg", field.dec_deg),
                       ("width_arcmin", field.width_arcmin)))

    def _run_brick_js(self, script, callback=None):
        """Fail closed on unexpected external navigations or missing JS API."""
        if (not self._brick_overlay_features or
                not self._brick_result_matches_target() or
                not hasattr(self.sky, "page")):
            return
        url = self.sky.url()
        if not (url.scheme() == "https" and
                url.host().lower() == "www.legacysurvey.org" and
                url.path().startswith("/viewer/")):
            return
        page = self.sky.page()
        if not hasattr(page, "runJavaScript"):
            return
        if callback is None:
            page.runJavaScript(script)
        else:
            page.runJavaScript(script, callback)

    def _install_bricks(self, attempt=0):
        if not self._brick_overlay_features or not self._brick_result_matches_target():
            return
        self._run_brick_js(
            installation_script(self._brick_overlay_features),
            lambda result: self._brick_install_result(result, attempt))

    def _brick_install_result(self, result, attempt):
        if result == "unavailable" and attempt < 6 and self.hits:
            QTimer.singleShot(450, lambda: self._install_bricks(attempt + 1))
        elif isinstance(result, str) and result.startswith("ready:"):
            self._selected_brick_changed()

    def _selected_brick_changed(self):
        """Highlight the selected survey brick and filter the FITS inventory."""
        row = self.bricks.currentRow()
        if self.bricks.selectedItems() and 0 <= row < len(self.hits):
            hit = self.hits[row]
            data_key = (hit.release, hit.region, hit.name)
            idx = next((
                i for i in range(self.brick_filter.count())
                if self.brick_filter.itemData(i) is not None and
                tuple(self.brick_filter.itemData(i)) == data_key
            ), -1)
            if idx >= 0:
                self.brick_filter.setCurrentIndex(idx)
        if hasattr(self, "science_qa"):
            chosen = (self.hits[row]
                      if self.bricks.selectedItems() and
                      0 <= row < len(self.hits) else None)
            previous = self.science_qa._hit
            self.science_qa.set_selected_hit(chosen)
            if previous != chosen:
                self._invalidate_qa_figure()
        key = (self._brick_overlay_keys[row]
               if self.bricks.selectedItems() and
                  0 <= row < len(self._brick_overlay_keys) else None)
        self._run_brick_js(highlight_script(key))
        self._queue_project_save()
        self._refresh_workflow()

    def _hover_brick_row(self, row, _column):
        key = (self._brick_overlay_keys[row]
               if 0 <= row < len(self._brick_overlay_keys) else None)
        self._run_brick_js(highlight_script(key, hover=True))

    def eventFilter(self, watched, event):
        if (hasattr(self, "bricks") and
                watched is self.bricks.viewport() and
                event.type() == QEvent.Leave):
            # Restore the persistent selected style when the table is left.
            self._run_brick_js(highlight_script(None, hover=True))
        return super().eventFilter(watched, event)

    def preview_brick(self, row: int, _column: int):
        """Preview a brick center; preserve current catalogue/selection state."""
        if row < 0 or row >= len(self.hits):
            return
        hit = self.hits[row]
        if self._local_project is not None:
            # No fictional brick centers from placeholder coordinates.
            self.message.setText(
                "This project was restored from local filenames only; "
                "official brick geometry is unavailable offline. "
                "Use Show target for the known ICRS position.")
            return
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
        if not self._webengine_isolated:
            self.sky.load(QUrl(link))
        self.message.setText(
            f"Previewing brick {hit.name} at RA={mid_ra:.5f}, "
            f"Dec={mid_dec:.5f}; target coordinates unchanged.")

    def open_viewer_in_browser(self):
        """Exact ICRS position and release, independent of QtWebEngine networking."""
        if self._validated_field() is None:
            return
        # External browser cannot receive the live Qt Leaflet overlay.
        # Include native static poly brick lines there as a fallback.
        QDesktopServices.openUrl(QUrl(self._viewer_link(include_bricks=True)))

    def _viewer_load_started(self):
        self.message.setText("Loading official Legacy Sky Viewer in Qt WebEngine…")

    def _viewer_load_finished(self, success: bool):
        if success:
            self._install_bricks()
            self.message.setText(
                "Legacy Viewer page loaded. This does not certify that all "
                "tiles or external scripts have loaded; inspect the image.")
        else:
            self.message.setText(
                "Embedded Legacy Viewer page failed. Use Open in browser "
                "to inspect it in a separate browser.")

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

    def _start(self, action, finished, on_error=None, *,
               operation_name="background"):
        """Compatibility wrapper; worker lifecycle belongs to controller."""
        return self._task_controller.start(
            action, finished, on_error, operation_name=operation_name)

    def _request_stop(self):
        return self._task_controller.request_stop()

    @Slot()
    def _idle(self):
        # This callback is invoked only after the GUI terminal delivery
        # and QThread shutdown have both been acknowledged.
        result = self._task_controller.idle()
        if hasattr(self, "photometry_workspace"):
            self.photometry_workspace.set_psf_busy(False)
        return result

    def _progress(self, item):
        if isinstance(item, tuple):
            done, total, path = item
            self.bar.setMaximum(max(total, 1))
            self.bar.setValue(done)
            self.message.setText(f"{done}/{total}: {path}")

    def _release_selection_changed(self, _=None):
        self._search_request = None
        # Changing requested releases invalidates discovery, never the
        # immutable original FITS or the project's source registry.
        if getattr(self, "_restoring_local_project", False):
            return
        if self.manifest is not None:
            self.hits = []
            self.manifest = None
            self.bricks.setRowCount(0)
            self.files.setRowCount(0)
            self.files.hide()
            self.download_btn.setEnabled(False)
            self.selected_label.setText("0 selected · run Find data")
            self.science_qa.set_selected_hit(None)
            self.science_btn.setEnabled(False)
            self._brick_overlay_features = []
            self._brick_overlay_keys = []
            self.data_drawer.hide()
            self.message.setText(
                "Release selection changed. Run Find data to refresh "
                "candidate FITS. Local files were not altered.")
            self._refresh_workflow()

    def search(self):
        field = self._validated_field()
        if field is None:
            return
        releases = tuple(dr for dr, box in self.release_boxes.items() if box.isChecked())
        if not releases:
            QMessageBox.warning(self, "Select a release", "Choose at least one data release.")
            return
        # Explicit archive search detaches the local-only, offline view.
        # Previous project metadata and original data remain on disk.
        self._project_save_timer.stop()
        self._local_project = None
        self.project_status.setText("Archive search · no local project selected")
        self.manifest = None
        self.science_qa.set_selected_hit(None)
        self.bricks.setRowCount(0)
        self.files.setRowCount(0)
        self.files.hide()
        self.message.setText("Finding survey coverage…")
        self._refresh_workflow()
        self.show_viewer()
        ticket = (field, releases)
        self._search_request = ticket

        def work(progress):
            hits = discover_bricks(field, releases)
            return hits, build_manifest(field, hits)

        def finished(result):
            now = tuple(dr for dr, box in self.release_boxes.items()
                        if box.isChecked())
            try:
                same_field = self._field() == field
            except ValueError:
                same_field = False
            if self._search_request != ticket or now != releases or not same_field:
                self.message.setText(
                    "An earlier release/coordinate search completed but its "
                    "results were not installed. Run Find data again.")
                return
            self._receive_search(result)

        work.operation_name = "survey_search"
        self._start(work, finished)

    def _receive_search(self, result):
        hits, manifest = result
        self.hits, self.manifest = hits, manifest
        self._brick_overlay_features, self._brick_overlay_keys = brick_shapes(hits)
        self.bricks.setRowCount(len(hits))
        for row, h in enumerate(hits):
            medians = "/".join(str(h.median_exposures.get(b) or 0)
                               if h.median_exposures.get(b) is not None else "—"
                               for b in ("g", "r", "i", "z"))
            for col, value in enumerate((h.release.upper(), h.region, h.name, medians)):
                self.bricks.setItem(row, col, QTableWidgetItem(str(value)))
        # The table is the source for release and NEXP context.
        # The map shows unique sky-brick outlines; no duplicate prose needed.
        self.bricks.clearSelection()
        self.science_qa.set_selected_hit(None)
        self.show_viewer()
        self._fill_brick_filters()
        self._fill_files()
        self.data_drawer.show()
        self._size_data_drawer()
        self.message.setText(
            f"{len(hits)} survey bricks; "
            f"{len(manifest['products'])} candidate archive products. "
            "Choose a brick and inspect the FITS list. "
            "URLs remain unverified until explicitly checked.")
        self._refresh_workflow()

    def _fill_brick_filters(self):
        return self._data_controller._fill_brick_filters()

    def _fill_files(self, *, checked_paths=None):
        self._data_controller._fill_files(checked_paths=checked_paths)
        self.files.setHidden(not bool(
            self.manifest and self.manifest.get("products")))

    def _mark_local_files(self):
        return self._data_controller._mark_local_files()

    @staticmethod
    def _short_http_evidence(evidence):
        return SurveyDataController.short_http_evidence(evidence)

    def _is_visible_product(self, entry):
        return self._data_controller._is_visible_product(entry)

    def _filter_products(self, *_):
        return self._data_controller._filter_products()

    def _set_visible_selection(self, checked: bool):
        return self._data_controller._set_visible_selection(checked)

    def select_science_quartet(self):
        return self._data_controller.select_science_quartet()

    def _clear_all_selection(self):
        return self._data_controller._clear_all_selection()

    def _selected(self):
        return self._data_controller._selected()

    def _update_selection_summary(self, *_):
        return self._data_controller._update_selection_summary()

    def _apply_profile(self, _=None):
        return self._data_controller._apply_profile()

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
                progress.checkpoint()
                results[entry["relative_path"]] = probe_product(entry)
                progress((i, len(entries), entry["relative_path"]))
            return results

        work.operation_name = "http_verify"
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
        if self._local_project is not None and selected.issubset(
                self._local_project.on_disk):
            self.message.setText(
                f"All {len(selected)} selected science products already "
                "exist locally. No network download was attempted.")
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
            self, "Choose the science data directory",
            str(self._local_project.data_root if self._local_project
                else self._object_project.root if self._object_project
                else Path.home()))
        if not directory:
            return
        manifest = json.loads(json.dumps(self.manifest))
        self._last_download_root = str(directory)
        self.bar.setValue(0)
        self.message.setText("Downloading original archive FITS files…")

        def work(progress):
            return download_manifest(
                manifest, Path(directory), product_paths=selected,
                progress=lambda a,b,c: progress((a,b,c)),
                cancel_check=progress.checkpoint)

        work.operation_name = "download"
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

        job.operation_name = "coverage"
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
        if self._last_download_root:
            row = self.bricks.currentRow()
            selected = (self.hits[row]
                        if self.bricks.selectedItems() and
                        0 <= row < len(self.hits) else None)
            self.science_qa.set_data_root(
                self._last_download_root,
                region=selected.region if selected is not None else None)
        successes = sum(x["state"] != "ERROR" for x in report["results"])
        failures = len(report["results"]) - successes
        self.message.setText(
            f"Download finished: {successes} files saved/reused, {failures} errors. "
            "See download-report.json.")
        if failures:
            QMessageBox.warning(self, "Incomplete download",
                f"{failures} files failed. Inspect download-report.json.")
        if successes and self._last_download_root:
            # A new download may follow an explicit target edit. Persist that
            # accepted object position *before* reopening its local inventory.
            # Never let stale 0.1 metadata replace the newly searched target.
            if self._object_project is not None:
                self._save_object_project_context()
                self._open_local_project_folder(
                    self._last_download_root, from_object=True)
            else:
                self._manual_target_edited = True
                self._open_local_project_folder(self._last_download_root)

    def _on_science_context_changed(self, *_):
        self._qa_generation += 1
        if hasattr(self, "science_qa"):
            self.science_qa.clear_result()
        self._invalidate_qa_figure()
        self._queue_project_save()

    def _invalidate_qa_figure(self, *_):
        if hasattr(self, "fits_explorer"):
            self.fits_explorer.clear_data()
        self._qa_pixmap = None
        self._comparison_requested = False
        if hasattr(self, "four_layer_comparison"):
            self.four_layer_comparison.clear_data()
        if hasattr(self, "multiband_comparison"):
            self.multiband_comparison.clear_data()
        if (hasattr(self, "comparison_btn") and
                self.preview_tabs.indexOf(self.four_layer_comparison) >= 0):
            self._close_four_comparison()
        if (hasattr(self, "multiband_comparison") and
                self.preview_tabs.indexOf(self.multiband_comparison) >= 0):
            self._close_multiband()
        self._refresh_workflow()

    def _preview_tab_changed(self, tab):
        # The project/coordinates sidebar remains a stable navigation
        # anchor even inside Photometry. Removing it forced the scientific
        # controls to stretch across the whole screen and hid the user's
        # familiar path back to the dataset browser. Preserve the original
        # sidebar and tabs, but suppress the redundant remote-sky heading.
        photometry_active = (
            self.preview_tabs.currentWidget() is self.photometry_workspace)
        self.navigation_scroll.show()
        self.preview_tabs.tabBar().show()
        self.preview_label.setVisible(not photometry_active)
        if tab != 0:
            # Survey downloads and Science QA live in a separate inspector.
            # Hide it when entering pixel analysis, so 1366px displays keep
            # adequate width for the actual scientific image.
            if self.data_drawer.isVisible():
                self.data_drawer.hide()
            self.preview_label.setText(
                "Data Manager · offline observational provenance"
                if self.preview_tabs.currentWidget() is self.dataset_manager
                else ("Photometry · PSF-0 diagnostic · no adopted measurements"
                      if self.preview_tabs.currentWidget() is self.photometry_workspace
                      else "Original FITS · read-only"))
        else:
            self.preview_label.setText(
                "Sky preview · not calibrated FITS")
        # Presentation changes must follow the active workspace. Keep
        # actual source selection and scientific workflow state untouched.
        self._refresh_workflow()

    def _close_four_comparison(self):
        tab = self.preview_tabs.indexOf(self.four_layer_comparison)
        if tab < 0:
            return
        self.preview_tabs.setCurrentWidget(self.fits_explorer)
        self.preview_tabs.removeTab(tab)
        self.four_layer_comparison.hide()
        self.four_layer_comparison.clear_data()
        self.comparison_btn.setText("Inspect data quality · 4 maps")

    def _open_four_comparison(self, dataset):
        if not self._qa_context_current(dataset):
            self.message.setText("The selected field or band changed. Reload FITS.")
            return
        try:
            self.four_layer_comparison.set_dataset(dataset)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Four-layer FITS comparison unavailable", str(exc))
            return
        if self.preview_tabs.indexOf(self.four_layer_comparison) < 0:
            self.preview_tabs.addTab(
                self.four_layer_comparison, "Data quality · 4 maps")
        self.preview_tabs.setCurrentWidget(self.four_layer_comparison)
        self.comparison_btn.setText("Close data quality")
        self.message.setText(
            "Interactive IMAGE / INVVAR / NEXP / MASKBITS comparison. "
            "Zoom, pan and hover on original FITS pixels.")

    def _toggle_four_comparison(self):
        if self.preview_tabs.indexOf(self.four_layer_comparison) >= 0:
            self._close_four_comparison()
            return
        dataset = self.fits_explorer._dataset
        if dataset is not None and self._qa_context_current(dataset):
            self._open_four_comparison(dataset)
            return
        # Load the original quartet once through the existing validated
        # science pathway, rather than generating or parsing a PNG.
        if self.thread is not None:
            QMessageBox.information(
                self, "Busy", "Finish the current operation before comparing.")
            return
        self._comparison_requested = True
        self.start_science_qa("explore")

    def _close_multiband(self):
        index = self.preview_tabs.indexOf(self.multiband_comparison)
        if index < 0:
            return
        self.preview_tabs.setCurrentWidget(self.fits_explorer)
        self.preview_tabs.removeTab(index)
        self.multiband_comparison.hide()
        self.multiband_comparison.clear_data()
        self.band_compare_btn.setText("Compare bands · g/r/i/z")

    def _toggle_multiband(self):
        """Inspect all locally available original bands for ONE selected brick.

        The worker performs a read-only FITS/WCS check on each complete
        quartet. Missing filters remain missing. No catalogue or HTTP call.
        """
        if self.preview_tabs.indexOf(self.multiband_comparison) >= 0:
            self._close_multiband()
            return
        if self.thread is not None:
            QMessageBox.information(
                self, "Operation running", "Finish the active task first.")
            return
        if not self._brick_result_matches_target():
            QMessageBox.warning(
                self, "Select a current brick",
                "Choose a brick belonging to the current ICRS target.")
            return
        row = self.bricks.currentRow()
        if not (self.bricks.selectedItems() and 0 <= row < len(self.hits)):
            QMessageBox.information(
                self, "Select a brick",
                "Select the exact release / region / brick to compare.")
            return
        field = self._validated_field()
        if field is None:
            return
        directory = self.science_qa.directory.text().strip()
        if not directory:
            QMessageBox.information(
                self, "Choose original FITS",
                "Select a local FITS directory before comparing bands.")
            return
        hit = self.hits[row]
        radii = (self.target_radius.value(), self.sky_inner.value(),
                 self.sky_outer.value())
        if not 0 < radii[0] <= radii[1] < radii[2] <= 1200:
            QMessageBox.warning(self, "Invalid aperture radii",
                                "Check target and sky-ring radii.")
            return
        context = self._current_science_context("multiband_display")
        if context is None:
            return
        ticket = self._qa_generation
        self.message.setText(
            "Loading local g/r/i/z FITS for WCS-linked comparison…")
        def job(progress):
            return load_multiband_originals(
                directory=directory, field=field, hit=hit, radii=radii,
                checkpoint=progress.checkpoint)
        self._start(
            job,
            lambda result: self._deliver_science_result(
                result, context=context, ticket=ticket,
                callback=self._multiband_ready),
            on_error=lambda message: self._deliver_multiband_error(
                message, context=context, ticket=ticket))

    def _deliver_multiband_error(self, message, *, context, ticket):
        if self._science_ticket_current(context=context, ticket=ticket):
            self.message.setText(
                "Could not compare original filters: " + message)

    def _multiband_ready(self, result):
        try:
            self.multiband_comparison.set_data(result)
        except (ValueError, RuntimeError) as exc:
            self.message.setText("Multi-band display rejected: " + str(exc))
            return
        if self.preview_tabs.indexOf(self.multiband_comparison) < 0:
            self.preview_tabs.addTab(
                self.multiband_comparison, "Compare optical bands")
        self.preview_tabs.setCurrentWidget(self.multiband_comparison)
        self.band_compare_btn.setText("Close band comparison")
        self.message.setText(
            "Read-only native optical bands. Linked by ICRS/WCS; "
            "no color calibration, PSF matching or adopted photometry.")
        self._refresh_workflow()

    def _review_science_history(self):
        """Read prior completed receipts without performing a new science run."""
        if self.thread is not None:
            QMessageBox.information(
                self, "Busy", "Finish the running operation before reviewing history.")
            return
        project = self._object_project
        root = (project.root if project is not None
                else self._local_project.root if self._local_project is not None
                else None)
        if root is None:
            QMessageBox.information(
                self, "No science project", "Open a project to review QA history.")
            return
        data_root = (project.data_root if project is not None
                     else self._local_project.data_root)
        project_id = project.project_id if project is not None else None
        contexts = {}
        for action in ("square", "census", "masks", "figure", "compare"):
            ctx = self._current_science_context(action)
            if ctx is not None:
                contexts[action] = ctx.to_dict()
        from lsbpipe.gui.run_contracts import discover_run_history
        from lsbpipe.gui.run_history_dialog import RunHistoryDialog
        self.message.setText("Reviewing saved scientific run provenance…")

        def job(progress):
            return discover_run_history(
                root, data_root=data_root, project_id=project_id,
                current_contexts=contexts)

        def finished(rows):
            if self._object_project is not project:
                self.message.setText(
                    "History review belongs to a previous project; ignored.")
                return
            dialog = RunHistoryDialog(rows, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self._run_history_dialog = dialog
            dialog.show()
            self.message.setText(
                f"Found {len(rows)} saved QA receipts; historical input hashes "
                "not automatically revalidated.")

        job.operation_name = "run_history"
        self._start(
            job, finished,
            on_error=lambda error: QMessageBox.warning(
                self, "Cannot open scientific run history", error))

    def _start_dataset_audit(self):
        """Standalone offline inspection: no selected brick is required."""
        from lsbpipe.surveys.dataset_integrity import (
            audit_dataset_root, save_dataset_audit)
        raw = self.science_qa.directory.text().strip()
        if not raw:
            QMessageBox.warning(
                self, "No original FITS folder",
                "Select the directory containing your Legacy FITS data.")
            return
        directory = Path(raw).expanduser().resolve()
        if directory.name != "data" and (directory / "data").is_dir():
            directory = directory / "data"
        project = self._object_project
        output_root = (project.root if project else
                       self._local_project.root if self._local_project else
                       directory.parent if directory.name == "data" else directory)
        project_id = project.project_id if project is not None else None
        try:
            # Draft targets may inspect the dataset inventory without a
            # footprint; half-edited positions must never be interpreted as
            # an adopted ICRS center.
            field = self._field() if (
                self.ra.text().strip() and self.dec.text().strip()) else None
            if bool(self.ra.text().strip()) != bool(self.dec.text().strip()):
                raise ValueError("Enter both RA and Dec for sky footprint")
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid ICRS field", str(exc))
            return
        ticket = self._qa_generation
        self.inspector_tabs.setCurrentWidget(self.science_qa)
        self.science_qa.clear_result()
        self.science_qa.mark_running("dataset_audit")
        self.science_qa.result.setPlainText(
            "Verifying native FITS HDUs/WCS and sampled multi-brick sky coverage…"
            "\nOriginals remain read-only. No archive requests.")
        self.message.setText("Auditing local FITS datasets…")

        def work(progress):
            report = audit_dataset_root(
                directory, field=field, pitch_arcsec=2.,
                progress=progress, cancel_check=progress.checkpoint)
            progress.checkpoint()
            path = save_dataset_audit(
                report, output_root, project_id=project_id)
            return report, path

        def finished(data):
            report, path = data
            if (ticket != self._qa_generation or
                    Path(self.science_qa.directory.text().strip()).expanduser().resolve()
                    not in (directory, directory.parent) or
                    self._object_project is not project):
                self.message.setText(
                    "Dataset audit completed for an older project selection; "
                    "saved receipt not displayed.")
                return
            validated = report["counts"]["validated"]
            total = report["counts"]["total"]
            groups = report["multi_brick_groups"]
            lines = [
                f"Local datasets: {total}; FITS/WCS validated: {validated}.",
                f"Missing, incomplete or invalid: "
                f"{report['counts']['incomplete_or_invalid']}.",
                "All outcomes are DIAGNOSTIC_NOT_ADOPTED.",
                "North and South remain separate data families.",
            ]
            for g in groups:
                lines.append(
                    f"{g['release'].upper()} {g['region']} {g['band']}: "
                    f"{100*g['geometric_sample_fraction']:.2f}% geometrically "
                    f"sampled; {100*g['instrumentally_usable_sample_fraction']:.2f}% "
                    f"instrumentally usable; {g['overlapping_geometric_samples']} "
                    "overlapping sampled cells.")
            self.science_qa.show_result({
                "summary": "\n".join(lines), "path": str(path),
                "manifest_path": str(path), "is_figure": False,
            })
            if project is not None:
                from lsbpipe.surveys.object_projects import save_object_project
                project.last_dataset_audit = {
                    "relative_path": path.relative_to(project.root).as_posix(),
                    "data_root": str(directory),
                    "created_utc": report["created_utc"],
                    "validated": validated,
                    "total": total,
                    "science_status": "DIAGNOSTIC_NOT_ADOPTED",
                }
                try:
                    save_object_project(project)
                except (ValueError, OSError) as exc:
                    self.project_status.setText(
                        f"Dataset audit saved but project index update failed: {exc}")
            self.message.setText(
                f"Dataset integrity audit saved: {path}")
            self.project_status.setText(
                f"FITS audit: {validated}/{total} quartets validated, "
                "not scientifically adopted.")

        work.operation_name = "dataset_audit"
        self._start(
            work, finished,
            on_error=lambda msg: self.science_qa.show_error(msg)
            if ticket == self._qa_generation else None)

    def start_science_qa(self, action: str):
        """Run an existing original-FITS scientific validator in a worker.

        No network calls; context is frozen before QThread starts. A stale
        target/brick selection can never be silently analyzed as new data.
        """
        if action == "compare_layers":
            self._toggle_four_comparison()
            return
        if action == "dataset_audit":
            if self.thread is not None:
                QMessageBox.information(
                    self, "Busy", "Another survey operation is running.")
                return
            self._start_dataset_audit()
            return
        if self.thread is not None:
            QMessageBox.information(
                self, "Busy", "Another survey operation is running.")
            return
        if not self._brick_result_matches_target():
            QMessageBox.warning(
                self, "Search first",
                "Search this ICRS target again before analyzing a selected "
                "brick. The previous result may belong to another field.")
            return
        row = self.bricks.currentRow()
        if (row < 0 or row >= len(self.hits) or
                not self.bricks.selectedItems()):
            QMessageBox.information(
                self, "Select a brick",
                "Select one release/region/brick row in the left table.")
            return
        field = self._validated_field()
        if field is None:
            return
        hit = self.hits[row]
        band = self.science_qa.current_band()
        directory = self.science_qa.directory.text().strip()
        north = self.science_qa.north_directory.text().strip()
        south = self.science_qa.south_directory.text().strip()
        if action == "compare":
            if not north or not south:
                QMessageBox.information(
                    self, "Choose both data folders",
                    "Select separate original DR9 North and South "
                    "download folders before comparing native pixels.")
                return
        elif not directory:
            QMessageBox.information(
                self, "Choose local FITS",
                "Select a downloaded FITS folder in the Science QA tab.")
            return
        target, inner, outer = (
            self.target_radius.value(),
            self.sky_inner.value(), self.sky_outer.value())
        if not 0 < target <= inner < outer <= 1200:
            QMessageBox.warning(
                self, "Invalid QA radii",
                "Require 0 < object radius <= sky inner < sky outer <= 1200 arcsec")
            return
        context = ScienceContext.capture(
            action=action, field=field, hit=hit, band=band,
            directory=directory, north_directory=north, south_directory=south,
            target_radius_arcsec=target, sky_inner_arcsec=inner,
            sky_outer_arcsec=outer,
            project_id=(self._object_project.project_id
                        if self._object_project is not None else None))
        ticket = self._qa_generation
        self.inspector_tabs.setCurrentWidget(self.science_qa)
        self.science_qa.clear_result()
        self.message.setText("Analyzing original local FITS · " + action + "…")
        self.science_qa.mark_running(action)
        self.science_qa.result.setPlainText(
            "Computing " + action + " from original local FITS…")
        if action == "explore":
            def job(progress):
                return prepare_fits_explorer(
                    directory=directory, field=field, hit=hit, band=band,
                    target_radius_arcsec=target,
                    sky_inner_arcsec=inner, sky_outer_arcsec=outer)
            self._start(
                job,
                lambda data: self._deliver_science_result(
                    data, context=context, ticket=ticket,
                    callback=self._fits_explorer_ready),
                on_error=lambda message: self._deliver_science_error(
                    message, context=context, ticket=ticket))
        else:
            def job(progress):
                return run_science_qa_backend(
                    action, field=field, hit=hit, band=band, directory=directory,
                    north_directory=north, south_directory=south,
                    target_radius_arcsec=target,
                    sky_inner_arcsec=inner, sky_outer_arcsec=outer,
                    context=context,
                    project_root=(self._object_project.root
                                  if self._object_project is not None else None))
            # Connect errors before the worker starts.
            self._start(
                job,
                lambda data: self._deliver_science_result(
                    data, context=context, ticket=ticket,
                    callback=self._science_qa_ready),
                on_error=lambda message: self._deliver_science_error(
                    message, context=context, ticket=ticket))

    def _current_science_context(self, action):
        return self._science_context_controller.current(action)

    def _science_ticket_current(self, *, context, ticket):
        return self._science_context_controller.ticket_current(
            context=context, ticket=ticket)

    def _deliver_science_result(self, result, *, context, ticket, callback):
        if not self._science_ticket_current(context=context, ticket=ticket):
            self.message.setText(
                "Previous science job finished: outdated selection ignored.")
            return
        if callback == self._science_qa_ready and result.get("context") != context:
            self.science_qa.show_error(
                "Science result has a different frozen input context.")
            return
        callback(result)

    def _deliver_science_error(self, message, *, context, ticket):
        self._comparison_requested = False
        if self._science_ticket_current(context=context, ticket=ticket):
            self.science_qa.show_error(message)

    def _fits_explorer_ready(self, dataset):
        if not self._qa_context_current(dataset):
            self.message.setText(
                "Discarded stale FITS view: selected field or brick changed.")
            self.science_qa.clear_result()
            return
        self.fits_explorer.set_dataset(dataset)
        self.preview_tabs.setCurrentWidget(self.fits_explorer)
        if self._comparison_requested:
            self._comparison_requested = False
            self._open_four_comparison(dataset)
        # An interactive view is not a new saved science report; do not
        # leave an old unrelated JSON report button looking current.
        self.science_qa.clear_result()
        self.science_qa.steps.setItemText(
            0, self.science_qa._step_names[0] + " · Viewer ready")
        self.science_qa.result.setPlainText(
            f"{dataset['source']} · original four-HDU FITS cutout\n"
            f"{dataset['pixel_shape'][1]} × {dataset['pixel_shape'][0]} pixels\n"
            "Use the center viewer to inspect individual source pixels.\n"
            "DISPLAY ONLY · DIAGNOSTIC_NOT_ADOPTED")
        self.message.setText(
            "Interactive original-FITS view loaded: " + dataset["source"])
        self._refresh_workflow()

    def _qa_context_current(self, dataset):
        return self._science_context_controller.raster_current(dataset)

    def _science_qa_ready(self, result):
        if not self._brick_result_matches_target():
            self.message.setText("Discarded stale QA result for edited field.")
            self.science_qa.clear_result()
            return
        row = self.bricks.currentRow()
        if (row < 0 or row >= len(self.hits) or
                not self.bricks.selectedItems()):
            self.science_qa.clear_result()
            return
        expected = (
            self.hits[row].release, self.hits[row].region,
            self.hits[row].name, self.science_qa.current_band())
        if result.get("provenance") != expected:
            self.science_qa.clear_result()
            return
        self.science_qa.show_result(result)
        latest = {
            "action": result["action"], "path": result["path"],
            "manifest_path": result.get("manifest_path", ""),
            "summary": result["summary"],
            "provenance": list(result["provenance"]),
            "run_id": result.get("run_id"),
            "project_id": (self._object_project.project_id
                           if self._object_project is not None else None),
        }
        if self._local_project is not None:
            self._local_project.last_science = latest
        if self._object_project is not None:
            self._object_project.last_science = latest
        self._queue_project_save()
        self.message.setText(
            "Local FITS QA saved (not adopted): " + result["path"])
        if result["is_figure"]:
            # Figure is an optional exported artifact, not the four-layer
            # workspace. The latter always renders native arrays interactively.
            self.message.setText(
                "Optional diagnostic PNG exported: " + result["path"] +
                " · Use Compare 4 layers for interactive inspection.")

    def closeEvent(self, event):
        if self.thread is not None:
            QMessageBox.information(self, "Download in progress",
                                    "Complete the active operation before closing.")
            event.ignore()
        else:
            self._project_save_timer.stop()
            self._save_project_state()
            if self._object_project is not None and not self._returning_to_hub:
                self.returnToProjects.emit()
                event.ignore()
                return
            # Children are hidden on QMainWindow.close(), but QWidget
            # closeEvent is not guaranteed on child FITSExplorer. Shut down
            # its executor explicitly before the parent can be destroyed.
            self._closing_task_ui = True
            self.fits_explorer.shutdown_renderer()
            self.four_layer_comparison.shutdown_renderer()
            self.multiband_comparison.shutdown_renderer()
            super().closeEvent(event)


def main():
    """Console script uses the same Project Hub as python -m lsbpipe.gui."""
    from .project_hub import main as launch_hub
    return launch_hub()
