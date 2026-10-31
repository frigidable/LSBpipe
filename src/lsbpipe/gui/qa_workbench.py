"""Compact, scrollable right-side Science QA workbench for original local FITS."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QVBoxLayout, QHBoxLayout, QPushButton,
    QComboBox, QFileDialog, QPlainTextEdit, QScrollArea, QSizePolicy,
)

from lsbpipe.surveys.legacy import BrickHit


class ScienceQAControls(QWidget):
    """UI state only; all expensive science work is handled by main worker."""

    requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("science_qa_workbench")
        self._hit = None
        self._busy = False
        self._last_path = None

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
        self.band.currentTextChanged.connect(self.clear_result)

        layout.addWidget(QLabel("Downloaded FITS folder"))
        self.directory, browse = self._directory_row(
            "science_fits_root", "Choose pilot folder or data/ root")
        layout.addLayout(browse)
        self.directory.setToolTip(
            "Choose the folder that contains dr9/.../coadd, or its pilot "
            "parent containing a data/ subfolder. FITS are never modified.")

        layout.addWidget(QLabel("Local scientific operations"))
        self.buttons = {}
        for key, caption, tooltip in (
            ("square", "Check full search square",
             "Original-FITS WCS coverage, NEXP and per-bit fractions"),
            ("masks", "Locate mask footprints",
             "Find connected GALAXY/MEDIUM/SATUR regions in sky coordinates"),
            ("explore", "Explore original FITS interactively",
             "Pixel readout in ICRS, zoom, layers and on/off mask overlays"),
            ("figure", "Export four-layer FITS map",
             "Save a static four-panel WCS figure, read-only"),
            ("compare", "Compare DR9 North / South",
             "Compare only identical native WCS pixel centers; no stacking"),
        ):
            button = QPushButton(caption)
            button.setObjectName("science_action_" + key)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _, action=key: self.requested.emit(action))
            layout.addWidget(button)
            self.buttons[key] = button

        pair_label = QLabel("North / South originals (paired comparison)")
        pair_label.setWordWrap(True)
        layout.addWidget(pair_label)
        self.north_directory, north = self._directory_row(
            "science_north_root", "North pilot folder or data/")
        layout.addLayout(north)
        self.south_directory, south = self._directory_row(
            "science_south_root", "South pilot folder or data/")
        layout.addLayout(south)
        help_text = QLabel(
            "Select the same DR9 brick and band. The comparator rejects "
            "different native WCS grids instead of silently reprojecting.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

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
        layout.addStretch()
        self._update_enabled()

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
        self.result.setPlainText(
            result["summary"] + "\n\n"
            "Saved locally:\n" + result["path"])
        self._last_path = result["path"]
        self.open_report.setText(
            "Open FITS image" if result["is_figure"] else "Open saved JSON")
        self.open_report.setEnabled(True)

    def show_error(self, error: str):
        self.result.setPlainText("Scientific QA failed:\n" + error)

    def clear_result(self):
        self._last_path = None
        self.open_report.setEnabled(False)
        self.result.setPlainText(
            "Target, brick or band selection changed. Verify original FITS "
            "provenance and rerun this diagnostic to avoid stale results.")

    def _open_last(self):
        if self._last_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_path))
