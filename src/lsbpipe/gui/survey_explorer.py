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
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
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
        split = QSplitter(Qt.Horizontal, self)
        left = QWidget()
        left.setObjectName("survey_navigation")
        left.setMinimumWidth(270)
        panel = QVBoxLayout(left)
        panel.setContentsMargins(10, 10, 10, 10)
        panel.setSpacing(8)

        target_box = QGroupBox("Target & search")
        form = QFormLayout(target_box)
        self.ra = QDoubleSpinBox()
        self.ra.setDecimals(6)
        self.ra.setRange(0, 359.999999)
        self.ra.setValue(154.7709)
        self.dec = QDoubleSpinBox()
        self.dec.setDecimals(6)
        self.dec.setRange(-90, 90)
        self.dec.setValue(46.4537)
        self.field_width = QDoubleSpinBox()
        self.field_width.setRange(0.1, 120.0)
        self.field_width.setDecimals(1)
        self.field_width.setValue(15.0)
        self.field_width.setSuffix(" arcmin")
        form.addRow("RA (ICRS, deg)", self.ra)
        form.addRow("Dec (ICRS, deg)", self.dec)
        form.addRow("Square field width", self.field_width)
        releases = QHBoxLayout()
        self.release_boxes = {}
        for dr in ("dr9", "dr10", "dr11"):
            check = QCheckBox(dr.upper())
            check.setChecked(dr == "dr9")
            self.release_boxes[dr] = check
            releases.addWidget(check)
        form.addRow("Releases", releases)
        actions = QHBoxLayout()
        self.search_btn = QPushButton("Find data")
        self.search_btn.setToolTip("Discover Legacy archive bricks for this ICRS field")
        self.search_btn.clicked.connect(self.search)
        actions.addWidget(self.search_btn)
        self.center_btn = QPushButton("Show target")
        self.center_btn.clicked.connect(self.show_viewer)
        actions.addWidget(self.center_btn)
        form.addRow(actions)
        secondary_actions = QHBoxLayout()
        self.diagnostic_btn = QPushButton("Viewer status")
        self.diagnostic_btn.clicked.connect(self.show_viewer_diagnostics)
        secondary_actions.addWidget(self.diagnostic_btn)
        self.external_btn = QPushButton("Open in browser")
        self.external_btn.clicked.connect(self.open_viewer_in_browser)
        secondary_actions.addWidget(self.external_btn)
        form.addRow(secondary_actions)
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

        self.summary = QLabel("No catalogs queried yet; no coverage claims.")
        self.summary.setWordWrap(True)
        panel.addWidget(self.summary)
        self.bricks = QTableWidget(0, 4)
        self.bricks.setHorizontalHeaderLabels(["DR", "Region", "Brick", "Median NEXP g/r/i/z"])
        self.bricks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bricks.setMinimumHeight(140)
        panel.addWidget(QLabel("Release-covered intersecting bricks"))
        panel.addWidget(self.bricks, 1)
        self.bricks.cellDoubleClicked.connect(self.preview_brick)
        panel.addWidget(QLabel("Double-click a brick to preview its center"))
        self.data_toggle = QPushButton("Files & coverage  ▾")
        self.data_toggle.setAccessibleName("Show or hide file selection and coverage")
        self.data_toggle.setToolTip("Open the download and local FITS coverage workbench")
        self.data_toggle.clicked.connect(self._toggle_data_drawer)
        panel.addWidget(self.data_toggle)
        panel.addStretch(1)

        # File selection and coverage are a separate, collapsible task surface.
        # They no longer set a 900+ px minimum width on the survey navigation.
        data_container = QWidget()
        data_panel = QVBoxLayout(data_container)
        data_panel.setContentsMargins(10, 8, 10, 8)
        data_panel.setSpacing(6)

        choices = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(["Standard: image, invvar, maskbits, nexp, Tractor",
                               "Extended: +psfsize, depth, galdepth, model"])
        self.profile.currentIndexChanged.connect(self._apply_profile)
        choices.addWidget(self.profile, 2)
        self.verify_btn = QPushButton("Check selected files")
        self.verify_btn.clicked.connect(self.verify)
        choices.addWidget(self.verify_btn)
        data_panel.addLayout(choices)
        filters = QHBoxLayout()
        self.brick_filter = QComboBox()
        self.brick_filter.addItem("All bricks")
        self.brick_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.brick_filter, 2)
        self.band_filter = QComboBox()
        self.band_filter.addItems(["All bands", "g", "r", "i", "z"])
        self.band_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.band_filter, 1)
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(
            ["All products", "image", "invvar", "maskbits", "nexp",
             "tractor", "psfsize", "depth", "galdepth", "model"])
        self.kind_filter.currentIndexChanged.connect(self._filter_products)
        filters.addWidget(self.kind_filter, 2)
        data_panel.addLayout(filters)

        selection_actions = QHBoxLayout()
        self.select_visible_btn = QPushButton("Select visible")
        self.select_visible_btn.clicked.connect(lambda: self._set_visible_selection(True))
        selection_actions.addWidget(self.select_visible_btn)
        self.clear_visible_btn = QPushButton("Clear visible")
        self.clear_visible_btn.clicked.connect(lambda: self._set_visible_selection(False))
        selection_actions.addWidget(self.clear_visible_btn)
        self.science_btn = QPushButton("Science quartet")
        self.science_btn.setToolTip(
            "Select IMAGE, INVVAR, NEXP, MASKBITS for one selected brick and band")
        self.science_btn.clicked.connect(self.select_science_quartet)
        selection_actions.addWidget(self.science_btn)
        self.selected_label = QLabel("0 files selected")
        selection_actions.addWidget(self.selected_label, 2)
        data_panel.addLayout(selection_actions)

        self.files = QTableWidget(0, 6)
        self.files.setHorizontalHeaderLabels(
            ["Use", "Brick", "Product", "Band", "Evidence", "Size"])
        self.files.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.files.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.files.setTextElideMode(Qt.ElideMiddle)
        self.files.setAlternatingRowColors(True)
        data_panel.addWidget(self.files, 3)

        qa_layout = QHBoxLayout()
        qa_layout.addWidget(QLabel("Coverage QA (arcsec)"))
        self.target_radius = QDoubleSpinBox()
        self.target_radius.setRange(1, 1000)
        self.target_radius.setDecimals(1)
        self.target_radius.setValue(60.)
        self.target_radius.setToolTip("Circular object screening region; not a fitted isophote")
        qa_layout.addWidget(self.target_radius)
        self.sky_inner = QDoubleSpinBox()
        self.sky_inner.setRange(1, 1100)
        self.sky_inner.setDecimals(1)
        self.sky_inner.setValue(90.)
        self.sky_inner.setToolTip("Inner radius of diagnostic sky ring")
        qa_layout.addWidget(self.sky_inner)
        self.sky_outer = QDoubleSpinBox()
        self.sky_outer.setRange(1, 1200)
        self.sky_outer.setDecimals(1)
        self.sky_outer.setValue(180.)
        self.sky_outer.setToolTip("Outer radius of diagnostic sky ring")
        qa_layout.addWidget(self.sky_outer)
        self.qa_btn = QPushButton("Inspect local FITS")
        self.qa_btn.setToolTip(
            "Analyse an already-downloaded brick using IMAGE, INVVAR, NEXP and MASKBITS")
        self.qa_btn.clicked.connect(self.inspect_coverage)
        qa_layout.addWidget(self.qa_btn)
        data_panel.addLayout(qa_layout)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save manifest")
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        self.download_btn = QPushButton("Download selected")
        self.download_btn.clicked.connect(self.download)
        bottom.addWidget(self.download_btn)
        data_panel.addLayout(bottom)
        self.data_drawer = QDockWidget("Science files · coverage diagnostics", self)
        self.data_drawer.setObjectName("science_data_drawer")
        self.data_drawer.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.data_drawer.setFeatures(
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.data_drawer.setWidget(data_container)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.data_drawer)
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
        self.sky.setPage(_DiagnosticWebPage(self.sky))
        self.sky.setZoomFactor(self.web_scale.value())
        self.sky.loadStarted.connect(self._viewer_load_started)
        self.sky.loadFinished.connect(self._viewer_load_finished)
        self.navigation_scroll = QScrollArea()
        self.navigation_scroll.setObjectName("survey_scroll")
        self.navigation_scroll.setWidgetResizable(True)
        self.navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation_scroll.setWidget(left)
        self.navigation_scroll.setMinimumWidth(290)
        self.navigation_scroll.setMaximumWidth(425)
        split.addWidget(self.navigation_scroll)
        split.addWidget(self.sky)
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
        self.data_drawer.setVisible(not self.data_drawer.isVisible())

    def _drawer_visibility_changed(self, visible):
        self.data_toggle.setText(
            "Hide files & coverage  ▴" if visible else "Files & coverage  ▾")

    def _apply_web_scale(self, value):
        """Web UI scaling, independent of world-coordinate zoom."""
        self.sky.setZoomFactor(float(value))

    def _field(self):
        return SkyField(self.ra.value(), self.dec.value(), self.field_width.value())

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

    def _viewer_link(self, *, field=None, release=None, region=None):
        selected = [name for name, box in self.release_boxes.items()
                    if box.isChecked()]
        release = release or (selected[0] if selected else 'dr9')
        if region is None:
            region = self._preferred_viewer_region(release)
        return viewer_url(field or self._field(), release=release,
                          region=region, view=self.view_mode.currentData(),
                          zoom=13)

    def show_viewer(self, *_):
        """Navigate Qt directly to the hosted viewer, never as an iframe."""
        link = self._viewer_link()
        self.message.setText(
            "Opening official Legacy Survey Viewer; FOV control is separate "
            "from the brick search area.")
        self.sky.load(QUrl(link))

    def preview_brick(self, row: int, _column: int):
        """Preview a brick center; preserve current catalogue/selection state."""
        if row < 0 or row >= len(self.hits):
            return
        hit = self.hits[row]
        mid_ra = (hit.ra1 + ((hit.ra2-hit.ra1) % 360.)/2.) % 360.
        mid_dec = (hit.dec1 + hit.dec2) / 2.
        field = SkyField(mid_ra, mid_dec, self.field_width.value())
        link = self._viewer_link(
            field=field, release=hit.release, region=hit.region)
        self.sky.load(QUrl(link))
        self.message.setText(
            f"Previewing brick {hit.name} at RA={mid_ra:.5f}, "
            f"Dec={mid_dec:.5f}; target coordinates unchanged.")

    def open_viewer_in_browser(self):
        """Exact ICRS position and release, independent of QtWebEngine networking."""
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
        field = self._field()
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
        """Display compact scientific identities; retain selection across profiles."""
        entries = self.manifest["products"] if self.manifest else []
        self.files.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            check = QCheckBox()
            check.setChecked(
                checked_paths is None or entry["relative_path"] in checked_paths)
            check.toggled.connect(self._update_selection_summary)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(3, 1, 3, 1)
            layout.addWidget(check, alignment=Qt.AlignCenter)
            self.files.setCellWidget(row, 0, holder)
            details = (
                (1, entry["brick"]), (2, entry["kind"]),
                (3, entry["band"] or "shared"), (4, entry["evidence"]),
                (5, "—"))
            for col, value in details:
                item = QTableWidgetItem(str(value))
                item.setToolTip(
                    entry["relative_path"] + chr(10) + entry["url"])
                self.files.setItem(row, col, item)
        self._filter_products()

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
            self.files.item(row, 4).setText(r["evidence"])
            size = r.get("size_bytes")
            self.files.item(row, 5).setText(
                f"{size / 1048576:.1f} MiB" if size is not None else "—")
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
        field = self._field()
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
