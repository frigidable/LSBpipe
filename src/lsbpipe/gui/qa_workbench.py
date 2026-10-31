"""Compact, scrollable right-side Science QA workbench for original local FITS."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QVBoxLayout, QHBoxLayout, QPushButton,
    QComboBox, QFileDialog, QPlainTextEdit, QScrollArea, QSizePolicy,
    QToolBox,
)

from lsbpipe.surveys.legacy import BrickHit


class ScienceQAControls(QWidget):
    """UI state only; all expensive science work is handled by main worker."""

    requested = Signal(str)
    contextChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("science_qa_workbench")
        self._hit = None
        self._busy = False
        self._last_path = None
        self._last_manifest_path = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroller)
        content = QWidget()
        scroller.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        title = QLabel("Original FITS · Science QA")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)
        note = QLabel(
            "Read-only diagnostics of downloaded IMAGE, INVVAR, NEXP "
            "and MASKBITS. No sky fitting or photometric acceptance.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.selection_label = QLabel(
            "Select an archive brick on the left to inspect its local files.")
        self.selection_label.setObjectName("science_selected_brick")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        layout.addWidget(QLabel("Optical band"))
        self.band = QComboBox()
        self.band.setObjectName("science_qa_band")
        self.band.setAccessibleName("Band for local FITS diagnostics")
        layout.addWidget(self.band)
        self.band.currentTextChanged.connect(self._context_edited)

        layout.addWidget(QLabel("Downloaded FITS folder"))
        self.directory, browse = self._directory_row(
            "science_fits_root", "Choose pilot folder or data/ root")
        layout.addLayout(browse)
        self.directory.setToolTip(
            "Choose the folder that contains dr9/.../coadd, or its pilot "
            "parent containing a data/ subfolder. FITS are never modified.")

        # Progressive scientific workflow: keep source provenance above,
        # and keep the diagnostics/result below, without mixing the paired
        # North/South file selectors into ordinary one-brick inspection.
        self.steps = QToolBox()
        self.steps.setObjectName("science_qa_workflow")
        self._step_names = (
            "1 · Inspect FITS",
            "2 · Review masks",
            "3 · Compare reductions",
        )
        self._active_step = None
        self._step_for_action = {
            "square": 0, "explore": 0, "figure": 0,
            "census": 1, "masks": 1, "compare": 2,
        }
        self.buttons = {}

        inspect_page = QWidget()
        inspect_layout = QVBoxLayout(inspect_page)
        inspect_layout.setContentsMargins(5, 8, 5, 8)
        inspect_layout.setSpacing(6)
        intro = QLabel(
            "Screen the full search square, then inspect the four original "
            "FITS layers with WCS and display-only mask overlays.")
        intro.setWordWrap(True)
        inspect_layout.addWidget(intro)
        for key, caption, tooltip in (
            ("square", "Check full search square",
             "Instrumental coverage and NEXP over the requested TAN square"),
            ("explore", "Explore original FITS interactively",
             "Zoom, pan, layers, ICRS pixel readout and mask warnings"),
            ("figure", "Export four-layer FITS map",
             "Read-only WCS-aligned static diagnostic PNG"),
        ):
            self._add_action(inspect_layout, key, caption, tooltip)
        self.steps.addItem(inspect_page, self._step_names[0])

        masks_page = QWidget()
        masks_layout = QVBoxLayout(masks_page)
        masks_layout.setContentsMargins(5, 8, 5, 8)
        masks_layout.setSpacing(6)
        mask_note = QLabel(
            "Locate original SGA GALAXY, Gaia MEDIUM, SATUR and ALLMASK "
            "footprints. These are diagnostic warnings, not target masks.")
        mask_note.setWordWrap(True)
        masks_layout.addWidget(mask_note)
        self._add_action(
            masks_layout, "census", "Quantify masks by sky region",
            "Exact original pixel counts, bit intersections and numeric "
            "validity in cutout, square, target and sky annulus")
        self._add_action(
            masks_layout, "masks", "Locate mask footprints",
            "Connected tagged regions, sky centroids and aperture overlaps")
        self.steps.addItem(masks_page, self._step_names[1])

        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        compare_layout.setContentsMargins(5, 8, 5, 8)
        compare_layout.setSpacing(6)
        compare_note = QLabel(
            "DR9 North / South: select two separate original-FITS roots. "
            "Reject WCS mismatch; do not stack coadds as independent data.")
        compare_note.setWordWrap(True)
        compare_layout.addWidget(compare_note)
        self.north_directory, north = self._directory_row(
            "science_north_root", "North pilot folder or data/")
        compare_layout.addLayout(north)
        self.south_directory, south = self._directory_row(
            "science_south_root", "South pilot folder or data/")
        compare_layout.addLayout(south)
        self._add_action(
            compare_layout, "compare", "Compare DR9 North / South",
            "Exact common WCS pixel centers, masks, NEXP and INVVAR")
        self.steps.addItem(compare_page, self._step_names[2])
        self.steps.setCurrentIndex(0)
        layout.addWidget(self.steps)
        for entry in (self.directory, self.north_directory, self.south_directory):
            entry.textChanged.connect(self._context_edited)

        layout.addWidget(QLabel("Latest diagnostic"))
        self.result = QPlainTextEdit()
        self.result.setObjectName("science_qa_result")
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(190)
        self.result.setPlainText(
            "No scientific QA has been run in this session.\n"
            "The hosted Legacy Viewer is a remote preview only.")
        layout.addWidget(self.result, 1)
        self.open_report = QPushButton("Open saved result")
        self.open_report.setEnabled(False)
        self.open_report.clicked.connect(self._open_last)
        layout.addWidget(self.open_report)
        self.open_manifest = QPushButton("View run provenance manifest")
        self.open_manifest.setObjectName("science_open_manifest")
        self.open_manifest.setToolTip(
            "Open the immutable run receipt: ICRS field, apertures, "
            "original FITS SHA-256, analysis parameters and artifact hash")
        self.open_manifest.setEnabled(False)
        self.open_manifest.clicked.connect(self._open_manifest)
        layout.addWidget(self.open_manifest)
        layout.addStretch()
        self._update_enabled()

    def _add_action(self, layout, key, caption, tooltip):
        button = QPushButton(caption)
        button.setObjectName("science_action_" + key)
        button.setToolTip(tooltip)
        button.clicked.connect(
            lambda _, action=key: self.requested.emit(action))
        layout.addWidget(button)
        self.buttons[key] = button

    def mark_running(self, action: str):
        step = self._step_for_action[action]
        self._active_step = step
        self.steps.setCurrentIndex(step)
        self.steps.setItemText(step, self._step_names[step] + " · Running")

    def _finish_step(self, status: str):
        if self._active_step is not None:
            step = self._active_step
            self.steps.setItemText(step, self._step_names[step] + " · " + status)
            self._active_step = None

    def _directory_row(self, name, placeholder):
        entry = QLineEdit()
        entry.setObjectName(name)
        entry.setPlaceholderText(placeholder)
        entry.setClearButtonEnabled(True)
        entry.setMinimumHeight(29)
        browse = QPushButton("…")
        browse.setToolTip("Browse local science FITS folders")
        browse.setFixedWidth(36)
        browse.clicked.connect(lambda: self._browse(entry))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(entry, 1)
        row.addWidget(browse)
        return entry, row

    def _browse(self, entry):
        selected = QFileDialog.getExistingDirectory(
            self, "Choose original Legacy science FITS folder",
            entry.text() or str(Path.home()))
        if selected:
            entry.setText(selected)

    def _context_edited(self, *_):
        self.clear_result()
        self.contextChanged.emit()

    def set_selected_hit(self, hit: BrickHit | None):
        changed = hit != self._hit
        self._hit = hit
        if changed:
            self.clear_result()
        self.band.blockSignals(True)
        old_band = self.band.currentText()
        self.band.clear()
        if hit is not None:
            self.selection_label.setText(
                f"{hit.release.upper()} · {hit.region} · {hit.name} "
                "— selected archive brick")
            self.band.addItems(list(hit.median_exposures))
            index = self.band.findText(old_band)
            if index >= 0:
                self.band.setCurrentIndex(index)
            elif self.band.findText("r") >= 0:
                self.band.setCurrentText("r")
        else:
            self.selection_label.setText(
                "Select a brick in the left table before running local QA.")
        self.band.blockSignals(False)
        self._update_enabled()
        if changed:
            self.contextChanged.emit()

    def current_band(self) -> str:
        return self.band.currentText()

    def set_data_root(self, directory: str | Path, *, region: str | None = None):
        value = str(directory)
        self.directory.setText(value)
        if region == "north":
            self.north_directory.setText(value)
        elif region == "south":
            self.south_directory.setText(value)

    def set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._update_enabled()

    def _update_enabled(self):
        for name, button in self.buttons.items():
            button.setEnabled(
                not self._busy and self._hit is not None and
                (name != "compare" or self._hit.release == "dr9"))

    def show_result(self, result: dict):
        self._finish_step("Diagnostic saved")
        run_id = result.get("run_id")
        manifest = result.get("manifest_path")
        receipt = (
            "\n\nRun ID: " + str(run_id) if run_id else "")
        if manifest:
            receipt += "\nInput/output provenance: " + str(manifest)
        self.result.setPlainText(
            result["summary"] + "\n\n"
            "Saved locally:\n" + result["path"] + receipt)
        self._last_path = result["path"]
        self._last_manifest_path = manifest
        self.open_manifest.setEnabled(bool(manifest))
        self.open_report.setText(
            "Open FITS image" if result["is_figure"] else "Open saved JSON")
        self.open_report.setEnabled(True)

    def show_error(self, error: str):
        self._finish_step("Failed")
        self._last_path = None
        self._last_manifest_path = None
        self.open_report.setEnabled(False)
        self.open_manifest.setEnabled(False)
        self.result.setPlainText("Scientific QA failed:\n" + error)

    def clear_result(self):
        self._active_step = None
        if hasattr(self, "steps"):
            for step, name in enumerate(self._step_names):
                self.steps.setItemText(step, name)
        self._last_path = None
        self._last_manifest_path = None
        self.open_report.setEnabled(False)
        self.open_manifest.setEnabled(False)
        self.result.setPlainText(
            "Target, brick or band selection changed. Verify original FITS "
            "provenance and rerun this diagnostic to avoid stale results.")

    def _open_last(self):
        if self._last_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_path))

    def _open_manifest(self):
        if self._last_manifest_path is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(self._last_manifest_path))
