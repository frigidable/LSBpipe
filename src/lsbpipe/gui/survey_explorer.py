"""PySide6 survey explorer: Aladin sky panel + archive discovery and downloads.

Requires the optional 'gui' extra, a working QtWebEngine and network access
for the Aladin script/HiPS tiles and the Legacy archive. No GUI dependency is
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

from lsbpipe.surveys.legacy import (
    SkyField, build_manifest, discover_bricks, save_manifest)
from lsbpipe.surveys.transfer import probe_product, download_manifest


_ALADIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<style>html,body,#aladin-lite-div{margin:0;width:100%;height:100%;background:#101925;}
#notice{position:absolute;left:12px;top:12px;color:#fff;background:#101925d9;
font:13px sans-serif;padding:7px;z-index:10;pointer-events:none}</style></head>
<body><div id="aladin-lite-div"></div><div id="notice">Connecting to Aladin Lite…</div>
<script src="https://code.jquery.com/jquery-1.12.1.min.js"></script>
<script src="https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js"></script>
<script>
window.lsbMapCentre = function(){return window.lsbAladin ? window.lsbAladin.getRaDec() : null;};
window.lsbGo = function(ra,dec,fov){
    if (!window.lsbAladin) return;
    window.lsbAladin.gotoRaDec(ra,dec);
    window.lsbAladin.setFov(fov);
};
window.lsbBricks = function(boxes){
    if (!window.lsbAladin) return;
    if (window.lsbOverlay && typeof window.lsbOverlay.removeAll === 'function'){
        window.lsbOverlay.removeAll();
    } else {
        window.lsbOverlay = A.graphicOverlay({color:'#51d8ba',lineWidth:2});
        window.lsbAladin.addOverlay(window.lsbOverlay);
    }
    boxes.forEach(b => {
        // RA=0 crossing requires separate polygons; for now do not plot
        // a pathological >=180 degree edge in the viewport.
        if (((b[1]-b[0]+360)%360) < 5) {
          window.lsbOverlay.addFootprints([A.polygon([
            [b[0],b[2]], [b[1],b[2]], [b[1],b[3]], [b[0],b[3]]
          ])]);
        }
    });
};
if (window.A){
    A.init.then(function(){
        window.lsbAladin=A.aladin('#aladin-lite-div',
          {survey:'P/DSS2/color',target:'154.7709 +46.4537',
           fov:0.5,cooFrame:'ICRSd',showReticle:true});
        document.getElementById('notice').style.display='none';
    }).catch(e => {document.getElementById('notice').textContent='Aladin error: '+e;});
} else {
    document.getElementById('notice').textContent='Aladin script unavailable; check internet access';
}
</script></body></html>"""


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
        self.setWindowTitle("LSBpipe — Survey Explorer (experimental)")
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
        self.center_btn = QPushButton("Use Aladin center")
        self.center_btn.clicked.connect(self.use_map_center)
        actions.addWidget(self.center_btn)
        form.addRow(actions)
        panel.addWidget(target_box)

        self.summary = QLabel("No catalogs queried yet; no coverage claims.")
        self.summary.setWordWrap(True)
        panel.addWidget(self.summary)
        self.bricks = QTableWidget(0, 4)
        self.bricks.setHorizontalHeaderLabels(["Release", "Region", "Brick", "Median NEXP g/r/i/z"])
        self.bricks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bricks.setMinimumHeight(140)
        panel.addWidget(QLabel("Release-covered intersecting bricks"))
        panel.addWidget(self.bricks, 1)

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
        self.files = QTableWidget(0, 4)
        self.files.setHorizontalHeaderLabels(["Use", "File", "Evidence", "Size"])
        self.files.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
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
        self.sky.setHtml(_ALADIN_HTML, QUrl("https://aladin.cds.unistra.fr/"))
        split.addWidget(left)
        split.addWidget(self.sky)
        split.setSizes([650, 810])
        self.setCentralWidget(split)
        self.ra.valueChanged.connect(self._goto_aladin)
        self.dec.valueChanged.connect(self._goto_aladin)
        self.width.valueChanged.connect(self._goto_aladin)
        self.search_btn.setDefault(True)

    def _field(self):
        return SkyField(self.ra.value(), self.dec.value(), self.width.value())

    def _goto_aladin(self, _=None):
        field = self._field()
        script = f"window.lsbGo({field.ra_deg!r},{field.dec_deg!r},{field.width_arcmin / 60!r});"
        self.sky.page().runJavaScript(script)

    def use_map_center(self):
        def done(values):
            if isinstance(values, list) and len(values) == 2:
                ra, dec = values
                if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
                    self.ra.setValue(ra % 360)
                    self.dec.setValue(dec)
        self.sky.page().runJavaScript("window.lsbMapCentre()", done)

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
        self._goto_aladin()

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
        self._fill_files()
        boxes = [[h.ra1, h.ra2, h.dec1, h.dec2] for h in hits]
        self.sky.page().runJavaScript("window.lsbBricks(" + json.dumps(boxes) + ");")
        self.message.setText(f"{len(manifest['products'])} candidate files")

    def _fill_files(self):
        entries = self.manifest["products"] if self.manifest else []
        self.files.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            check = QCheckBox()
            check.setChecked(True)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(3, 1, 3, 1)
            layout.addWidget(check, alignment=Qt.AlignCenter)
            self.files.setCellWidget(row, 0, holder)
            for col, value in ((1, entry["relative_path"]),
                               (2, entry["evidence"]), (3, "—")):
                self.files.setItem(row, col, QTableWidgetItem(str(value)))

    def _selected(self):
        if not self.manifest:
            return set()
        return {
            self.manifest["products"][row]["relative_path"]
            for row in range(self.files.rowCount())
            if self.files.cellWidget(row, 0).findChild(QCheckBox).isChecked()
        }

    def _apply_profile(self, _=None):
        if self.manifest is None:
            return
        from lsbpipe.surveys.legacy import build_manifest, DEFAULT_PRODUCTS
        kinds = DEFAULT_PRODUCTS if self.profile.currentIndex() == 0 else (
            DEFAULT_PRODUCTS + ("psfsize", "depth", "galdepth", "model"))
        self.manifest = build_manifest(self._field(), self.hits, kinds=kinds)
        self._fill_files()

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
            self.files.item(row, 2).setText(r["evidence"])
            size = r.get("size_bytes")
            self.files.item(row, 3).setText(
                f"{size / 1048576:.1f} MiB" if size is not None else "—")
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
