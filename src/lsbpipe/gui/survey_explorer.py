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
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView)
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
        panel = QVBoxLayout(left)

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
        self.width = QDoubleSpinBox()
        self.width.setRange(0.1, 120.0)
        self.width.setDecimals(1)
        self.width.setValue(15.0)
        self.width.setSuffix(" arcmin")
        form.addRow("RA (ICRS, deg)", self.ra)
        form.addRow("Dec (ICRS, deg)", self.dec)
        form.addRow("Square field width", self.width)
        releases = QHBoxLayout()
        self.release_boxes = {}
        for dr in ("dr9", "dr10", "dr11"):
            check = QCheckBox(dr.upper())
            check.setChecked(dr == "dr9")
            self.release_boxes[dr] = check
            releases.addWidget(check)
        form.addRow("Releases", releases)
        actions = QHBoxLayout()
        self.search_btn = QPushButton("Find Legacy data")
        self.search_btn.clicked.connect(self.search)
        actions.addWidget(self.search_btn)
        self.center_btn = QPushButton("Show target in viewer")
        self.center_btn.clicked.connect(self.show_viewer)
        actions.addWidget(self.center_btn)
        self.diagnostic_btn = QPushButton("Viewer status")
        self.diagnostic_btn.clicked.connect(self.show_viewer_diagnostics)
        actions.addWidget(self.diagnostic_btn)
        self.external_btn = QPushButton("Open in browser")
        self.external_btn.clicked.connect(self.open_viewer_in_browser)
        actions.addWidget(self.external_btn)
        form.addRow(actions)
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
        self.bricks.setHorizontalHeaderLabels(["Release", "Region", "Brick", "Median NEXP g/r/i/z"])
        self.bricks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bricks.setMinimumHeight(140)
        panel.addWidget(QLabel("Release-covered intersecting bricks"))
        panel.addWidget(self.bricks, 1)
        self.bricks.cellDoubleClicked.connect(self.preview_brick)
        panel.addWidget(QLabel("Double-click a brick to inspect its center in Viewer"))

        choices = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(["Standard: image, invvar, maskbits, nexp, Tractor",
                               "Extended: +psfsize, depth, galdepth, model"])
        self.profile.currentIndexChanged.connect(self._apply_profile)
        choices.addWidget(self.profile, 2)
        self.verify_btn = QPushButton("Check selected files")
        self.verify_btn.clicked.connect(self.verify)
        choices.addWidget(self.verify_btn)
        panel.addLayout(choices)
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
        panel.addLayout(filters)

        selection_actions = QHBoxLayout()
        self.select_visible_btn = QPushButton("Select visible")
        self.select_visible_btn.clicked.connect(lambda: self._set_visible_selection(True))
        selection_actions.addWidget(self.select_visible_btn)
        self.clear_visible_btn = QPushButton("Clear visible")
        self.clear_visible_btn.clicked.connect(lambda: self._set_visible_selection(False))
        selection_actions.addWidget(self.clear_visible_btn)
        self.selected_label = QLabel("0 files selected")
        selection_actions.addWidget(self.selected_label, 2)
        panel.addLayout(selection_actions)

        self.files = QTableWidget(0, 6)
        self.files.setHorizontalHeaderLabels(
            ["Use", "Brick", "Product", "Band", "Evidence", "Size"])
        self.files.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.files.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.files.setTextElideMode(Qt.ElideMiddle)
        self.files.setAlternatingRowColors(True)
        panel.addWidget(self.files, 3)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save manifest")
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        self.download_btn = QPushButton("Download selected")
        self.download_btn.clicked.connect(self.download)
        bottom.addWidget(self.download_btn)
        panel.addLayout(bottom)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        panel.addWidget(self.bar)
        self.message = QLabel("Ready")
        panel.addWidget(self.message)

        self.sky = QWebEngineView()
        self.sky.setPage(_DiagnosticWebPage(self.sky))
        self.sky.setZoomFactor(self.web_scale.value())
        self.sky.loadStarted.connect(self._viewer_load_started)
        self.sky.loadFinished.connect(self._viewer_load_finished)
        split.addWidget(left)
        split.addWidget(self.sky)
        split.setSizes([650, 810])
        self.setCentralWidget(split)
        self.search_btn.setDefault(True)
        # Navigation to a remote web application is deliberately explicit.
        # RA/Dec spinbox edits must not trigger many remote page reloads.
        self.show_viewer()

    def _apply_web_scale(self, value):
        """Web UI scaling, independent of world-coordinate zoom."""
        self.sky.setZoomFactor(float(value))

    def _field(self):
        return SkyField(self.ra.value(), self.dec.value(), self.width.value())

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
        field = SkyField(mid_ra, mid_dec, self.width.value())
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
        self.summary.setText(
            f"{len(hits)} release/region brick intersections; "
            "NEXP values are brick medians, not pixel coverage. "
            "File URLs are unverified until checked.")
        self._fill_brick_filters()
        self._fill_files()
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
