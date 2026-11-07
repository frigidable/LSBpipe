"""Project-bound Photometry Workspace: preparation and provenance only.

No photometric algorithms are exposed before their validation gates exist.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import platform
import sys

import numpy as np

from PySide6.QtCore import Qt, Signal, QSaveFile, QIODevice
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QGroupBox, QHBoxLayout,
    QComboBox, QHeaderView, QLabel, QPushButton, QScrollArea, QTabBar, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy,
)

from lsbpipe.photometry.readiness import inspect_photometry_inputs
from lsbpipe.gui.psf0_panel import PSF0Panel
from lsbpipe.gui.psf0_full_qa import detailed_band_report

_STAGES = (
    ("01", "Data preparation", "Original FITS, WCS, native masks and calibration"),
    ("02", "Scene initialization", "Tentative sources, PSF star candidates and target protection"),
    ("03", "Preliminary sky", "Detection background and local star-cutout sky"),
    ("04", "PSF reconstruction", "Empirical per-band PSF, spatial QA and extended wings"),
    ("05", "Scene & smart masks", "PSF-aware contaminants and reviewed manual masks"),
    ("06", "Sky reconstruction", "Galaxy residual sky, holdouts and diffuse-light degeneracy"),
    ("07", "Refinement & QA", "Conditionally revisit PSF, masks, sky and uncertainty"),
    ("08", "Galaxy photometry", "Isophotes, structural models, colors and science results"),
)


class PhotometryWorkspace(QWidget):
    """Dedicated screen; never presents input-file presence as science-ready."""

    backRequested = Signal()
    sourcesRequested = Signal()
    psf0RunRequested = Signal(object)
    psf0SaveRequested = Signal(object)
    psf0AutoFwhmRequested = Signal(object)
    psf0TractorRequested = Signal(object)
    psf0BatchRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("photometry_workspace")
        self._project_root = None
        self._bound_project_id = None
        self._bound_source_identity = None
        self._tab_source_identity = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        self.back_btn = QPushButton("← Galaxy Workspace")
        self.back_btn.setObjectName("photometry_back_to_workspace")
        self.back_btn.clicked.connect(self.backRequested.emit)
        header.addWidget(self.back_btn)
        header.addStretch(1)
        self.sources_btn = QPushButton("Manage sources")
        self.sources_btn.setObjectName("photometry_manage_sources")
        self.sources_btn.clicked.connect(self.sourcesRequested.emit)
        header.addWidget(self.sources_btn)
        self.refresh_btn = QPushButton("Refresh saved selection")
        self.refresh_btn.setObjectName("photometry_refresh")
        self.refresh_btn.setToolTip(
            "Read project metadata and local file existence, without "
            "opening FITS, connecting to the archive or running analysis.")
        self.refresh_btn.clicked.connect(lambda _checked=False: self.refresh(force=True))
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        self.title = QLabel("Photometry")
        self.title.setObjectName("photometry_heading")
        self.title.setStyleSheet("font-size: 21px; font-weight: 600;")
        root.addWidget(self.title)
        self.subtitle = QLabel(
            "PSF-0 diagnostics are experimental · no adopted PSF, sky or galaxy photometry")
        self.subtitle.setObjectName("photometry_not_adopted")
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)

        # One persistent selector, not a tab strip that disappears behind
        # the scroll area when the user examines PSF settings or QA.
        self.band_selector = QTabBar(self)
        self.band_selector.setObjectName("psf0_sticky_band_selector")
        self.band_selector.setDocumentMode(True)
        self.band_selector.setExpanding(False)
        self.band_selector.setToolTip(
            "Select g/r/i/z to inspect its independent PSF model, "
            "radial comparison and scientific rejection status.")
        root.addWidget(self.band_selector)

        # The original eight unclickable scientific stage cards consumed
        # almost a third of the screen. Keep the roadmap accessible, but
        # the ordinary PSF workflow must have one primary work area.
        self.workflow_toggle = QPushButton("Scientific roadmap (8 stages)  ▸")
        self.workflow_toggle.setObjectName("photometry_workflow_toggle")
        self.workflow_toggle.setCheckable(True)
        self.workflow_toggle.setMaximumWidth(325)
        self.workflow_details = QGroupBox("Scientific workflow · planned modules")
        self.workflow_details.setObjectName("photometry_workflow_details")
        workflow_layout = QVBoxLayout(self.workflow_details)
        for number, name, description in _STAGES:
            block = QGroupBox(f"{number} · {name}")
            block.setObjectName(f"photometry_stage_{number}")
            stage = QVBoxLayout(block)
            caption = QLabel(description)
            caption.setWordWrap(True)
            caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
            stage.addWidget(caption)
            workflow_layout.addWidget(block)
        workflow_layout.addWidget(QLabel(
            "Only PSF-0 preview is currently implemented. Later stages "
            "remain plans, not executable actions."))
        self.workflow_details.setVisible(False)
        self.workflow_details.setMaximumWidth(980)
        self.workflow_toggle.toggled.connect(
            self.workflow_details.setVisible)
        self.workflow_toggle.toggled.connect(
            lambda checked: self.workflow_toggle.setText(
                "Scientific roadmap (8 stages)  ▾" if checked else
                "Scientific roadmap (8 stages)  ▸"))
        center_scroll = QScrollArea()
        center_scroll.setFrameShape(QScrollArea.NoFrame)
        center_scroll.setMinimumWidth(0)
        center_scroll.setWidgetResizable(True)
        center_scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        central = QWidget()
        central.setObjectName("photometry_narrow_science_column")
        # Fill the actual space beside the persistent survey sidebar.
        # The former 980-px cap created wide, useless outer gutters on
        # standard monitors; inner controls retain their own width limits.
        central.setMaximumWidth(1400)
        central.setMinimumWidth(0)
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        body = QVBoxLayout(central)
        body.setContentsMargins(8, 2, 8, 2)
        body.setSpacing(12)
        mode_row = QHBoxLayout()
        mode_caption = QLabel("Workspace view")
        mode_caption.setObjectName("psf0_workspace_view_caption")
        mode_row.addWidget(mode_caption)
        self.view_mode = QComboBox()
        self.view_mode.setObjectName("psf0_workspace_view_mode")
        self.view_mode.addItem("Guided · essentials", "guided")
        self.view_mode.addItem("Research · all controls", "research")
        self.view_mode.setToolTip(
            "Changes which controls are visible, never the PSF recipe, "
            "source FITS, independent holdouts or scientific QA.")
        mode_row.addWidget(self.view_mode)
        mode_row.addStretch(1)
        body.addLayout(mode_row)
        # Keep the optional scientific roadmap aligned to the same narrow
        # content column as the PSF controls instead of spanning the window.
        body.addWidget(self.workflow_toggle, 0, Qt.AlignLeft)
        body.addWidget(self.workflow_details)

        source_box = QGroupBox("Observation selection · saved project state")
        self.source_box = source_box
        source_layout = QVBoxLayout(source_box)
        self.source_identity = QLabel("No project bound")
        self.source_identity.setObjectName("photometry_source_identity")
        self.source_identity.setWordWrap(True)
        source_layout.addWidget(self.source_identity)
        self.source_path = QLabel("")
        self.source_path.setObjectName("photometry_source_path")
        self.source_path.setWordWrap(True)
        self.source_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        source_layout.addWidget(self.source_path)
        self.source_state = QLabel("No observational input checked")
        self.source_state.setObjectName("photometry_source_state")
        self.source_state.setWordWrap(True)
        source_layout.addWidget(self.source_state)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("photometry_quartet_table")
        self.table.setHorizontalHeaderLabels(["Original FITS", "Presence", "Path"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(175)
        source_layout.addWidget(self.table)
        self.source_details_toggle = QPushButton(
            "Observation and original FITS details  ▸")
        self.source_details_toggle.setObjectName(
            "photometry_source_details_toggle")
        self.source_details_toggle.setCheckable(True)
        self.source_details_toggle.setMaximumWidth(390)
        self.source_details_toggle.toggled.connect(source_box.setVisible)
        self.source_details_toggle.toggled.connect(
            lambda checked: self.source_details_toggle.setText(
                "Observation and original FITS details  ▾" if checked else
                "Observation and original FITS details  ▸"))
        source_box.setVisible(False)
        body.addWidget(self.source_details_toggle, 0, Qt.AlignLeft)
        body.addWidget(source_box)

        # Four scientific objects persist, but only one filter is visible
        # at once. A band never shares its preview, exclusions or QA
        # with another band, and batch runs still visit all four.

        batch = QGroupBox("PSF preview · optical filters")
        batch.setObjectName("psf0_four_band_toolbar")
        batch_layout = QVBoxLayout(batch)
        batch_note = QLabel(
            "Batch processing · original griz FITS remain unchanged. "
            "Each band keeps independent non-adopted results.")
        batch_note.setWordWrap(True)
        batch_layout.addWidget(batch_note)
        batch_buttons = QHBoxLayout()
        self.build_all_psfs_btn = QPushButton("Run all available bands")
        self.build_all_psfs_btn.setObjectName("psf0_build_all_bands")
        self.build_all_psfs_btn.setToolTip(
            "Runs one real native FITS/Tractor PSF-0 diagnostic for each "
            "available band, sequentially, in the existing background "
            "worker. Errors are isolated by band. No automatic downloads, "
            "science adoption or FITS export.")
        self.build_all_psfs_btn.clicked.connect(self._request_all_psfs)
        batch_buttons.addWidget(self.build_all_psfs_btn)
        self.copy_full_qa_btn = QPushButton("Copy complete QA")
        self.copy_full_qa_btn.setObjectName("psf0_copy_full_griz_debug")
        self.copy_full_qa_btn.clicked.connect(self.copy_full_qa)
        self.copy_full_qa_btn.setToolTip(
            "Copy full settings, all band outcomes, source evidence, "
            "star IDs, Gaia identity, sky metrics and signed per-star core/"
            "wing residual diagnostics as JSON. No native image pixels.")
        batch_buttons.addWidget(self.copy_full_qa_btn)
        self.save_full_qa_btn = QPushButton("Export QA JSON…")
        self.save_full_qa_btn.setObjectName("psf0_save_full_griz_debug")
        self.save_full_qa_btn.clicked.connect(self.save_full_qa)
        self.save_full_qa_btn.setToolTip(
            "Export the same full four-filter diagnostic JSON as an "
            "explicit user-selected new .json file, for ChatGPT attachment. "
            "Original FITS files are never modified.")
        batch_buttons.addWidget(self.save_full_qa_btn)
        batch_buttons.addStretch(1)
        batch_layout.addLayout(batch_buttons)
        self.batch_status = QLabel("No batch run started. Original FITS unchanged.")
        self.batch_status.setObjectName("psf0_griz_batch_status")
        self.batch_status.setWordWrap(True)
        batch_layout.addWidget(self.batch_status)
        self.psf0_panels = {}
        self.band_tabs = QTabWidget(central)
        self.band_tabs.setObjectName("psf0_single_band_tabs")
        self.band_tabs.setDocumentMode(True)
        self.band_tabs.setMinimumWidth(0)
        # The tab's initial three-card horizontal size hint must not
        # impose a hard minimum on the entire desktop window. Reflow is
        # decided by the parent before the tab gets its viewport.
        self.band_tabs.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Maximum)
        for native_band in ("g", "r", "i", "z"):
            panel = PSF0Panel(self.band_tabs, fixed_band=native_band)
            panel.setObjectName("psf0_panel_" + native_band)
            panel.runRequested.connect(self.psf0RunRequested.emit)
            panel.saveRequested.connect(self.psf0SaveRequested.emit)
            panel.autoFwhmRequested.connect(self.psf0AutoFwhmRequested.emit)
            panel.tractorRequested.connect(self.psf0TractorRequested.emit)
            self.psf0_panels[native_band] = panel
            index = self.band_tabs.addTab(panel, native_band.upper())
            panel.overviewChanged.connect(self._update_band_tab_statuses)
            self.band_tabs.setTabToolTip(
                index, "Independent " + native_band + "-band settings, "
                "scientific QA and non-adopted preview")
            self.band_selector.addTab(native_band.upper())
        # The fixed selector above the scroll replaces this inner tab bar;
        # both selectors use the same existing QTabWidget state.
        self.band_tabs.tabBar().hide()
        self.band_selector.currentChanged.connect(
            self.band_tabs.setCurrentIndex)
        self.band_tabs.currentChanged.connect(
            self.band_selector.setCurrentIndex)
        body.addWidget(self.band_tabs)
        body.addWidget(batch)
        self.view_mode.currentIndexChanged.connect(
            self._set_view_mode)
        # Compatibility alias for older callers; resolves to the first
        # available band on bind, not a second shared scientific panel.
        self.psf0_panel = self.psf0_panels["g"]

        method_box = QGroupBox("Measurement design")
        self.method_box = method_box
        method_layout = QVBoxLayout(method_box)
        for heading, detail in (
            ("Preparatory scene",
             "Identify preliminary sources and target-protected regions; "
             "pre-PSF user mask drafts are provisional and never science-adopted."),
            ("Star-cutout sky",
             "Estimate local residual backgrounds before building an empirical PSF. "
             "Keep this distinct from the final galaxy sky solution."),
            ("PSF before final masks",
             "Compare global, local and spatial models per filter with held-out "
             "stars; evaluate extended wings and survey-specific halo subtraction."),
            ("Final scene and sky",
             "Review PSF-aware contaminant masks, refine residual galaxy sky, "
             "and conditionally revisit upstream models when QA demands."),
            ("Photometry & final evidence",
             "Measure fixed/free isophotes and PSF-convolved models; propagate "
             "mask, sky and PSF uncertainty with immutable run provenance."),
        ):
            line = QLabel(f"<b>{heading}</b> — {detail}")
            line.setTextFormat(Qt.RichText)
            line.setWordWrap(True)
            line.setMargin(5)
            method_layout.addWidget(line)
        self.method_details_toggle = QPushButton(
            "Scientific methodology and planned steps  ▸")
        self.method_details_toggle.setObjectName(
            "photometry_methodology_toggle")
        self.method_details_toggle.setCheckable(True)
        self.method_details_toggle.setMaximumWidth(390)
        self.method_details_toggle.toggled.connect(method_box.setVisible)
        self.method_details_toggle.toggled.connect(
            lambda checked: self.method_details_toggle.setText(
                "Scientific methodology and planned steps  ▾" if checked else
                "Scientific methodology and planned steps  ▸"))
        method_box.setVisible(False)
        body.addWidget(self.method_details_toggle, 0, Qt.AlignLeft)
        body.addWidget(method_box)

        self.blocker = QLabel(
            "PSF-0 preview is experimental and not adopted science. "
            "No final background, masks, photometry or SBF is implemented. "
            "File presence alone never establishes source-FITS validity.")
        self.blocker.setObjectName("photometry_blocker")
        self.blocker.setWordWrap(True)
        self.blocker.setVisible(False)
        body.addWidget(self.blocker)
        center_scroll.setWidget(central)
        root.addWidget(center_scroll, 1)
        self._set_view_mode()
        self._update_band_tab_statuses()
        self._apply_science_reflow()

    def _apply_science_reflow(self):
        if hasattr(self, "psf0_panels"):
            # Decide from the available workspace viewport BEFORE the
            # inner tab attempts to enforce a horizontal minimum width.
            narrow = self.width() < 1140
            for panel in self.psf0_panels.values():
                panel.set_narrow_visual_layout(narrow)

    def resizeEvent(self, event):
        self._apply_science_reflow()
        super().resizeEvent(event)

    def _set_view_mode(self, *_):
        research = self.view_mode.currentData() == "research"
        for panel in self.psf0_panels.values():
            panel.set_research_mode(research)
        # Make the selected band's science panels the default workflow;
        # help and methodological details stay available in Research.
        self.workflow_toggle.setVisible(research)
        self.workflow_details.setVisible(
            research and self.workflow_toggle.isChecked())
        self.source_details_toggle.setVisible(research)
        self.source_box.setVisible(
            research and self.source_details_toggle.isChecked())
        self.method_details_toggle.setVisible(research)
        self.method_box.setVisible(
            research and self.method_details_toggle.isChecked())
        self.copy_full_qa_btn.setVisible(research)
        # Complete JSON export remains accessible in Guided mode.
        self._update_band_tab_statuses()

    def _update_band_tab_statuses(self):
        for band, panel in self.psf0_panels.items():
            index = self.band_tabs.indexOf(panel)
            if index < 0:
                continue
            state = panel.overview_state()
            label = f"{band.upper()} · {state}"
            tooltip = (
                f"{band}-band: {state}. Independent controls, "
                "science result and source provenance; not adopted.")
            self.band_tabs.setTabText(index, label)
            self.band_tabs.setTabToolTip(index, tooltip)
            self.band_selector.setTabText(index, label)
            self.band_selector.setTabToolTip(index, tooltip)

    def set_psf_busy(self, state):
        for panel in self.psf0_panels.values():
            panel.set_busy(state)
        self.build_all_psfs_btn.setEnabled(
            not state and any(panel.run_btn.isEnabled()
                              for panel in self.psf0_panels.values()))
        self.copy_full_qa_btn.setEnabled(not state)
        self.save_full_qa_btn.setEnabled(not state)

    def _request_all_psfs(self):
        if self._project_root is None:
            self.batch_status.setText("Open Leo P in Galaxy Projects first.")
            return
        requests, skipped = [], []
        for band, panel in self.psf0_panels.items():
            request = panel.make_run_request()
            if request is None:
                skipped.append({"band":band, "reason":"NATIVE_QUARTET_UNAVAILABLE"})
            elif not panel.run_btn.isEnabled():
                skipped.append({"band":band, "reason":"CATALOGUE_OR_WORKER_NOT_READY"})
            else:
                requests.append(request)
        if not requests:
            self.batch_status.setText(
                "No complete native FITS bands with required Gaia/Tractor "
                "catalogue. Manage sources; no science run started.")
            return
        self.batch_status.setText(
            f"Queued {len(requests)} available optical bands; "
            f"unavailable: {', '.join(x['band'] for x in skipped) or 'none'}. "
            "Diagnostic models only; no FITS exports.")
        for request in requests:
            self.psf0_panels[request["band"]].prepare_run_display()
        self.psf0BatchRequested.emit({
            "project_root":str(self._project_root),
            "requests":requests,
            "skipped_bands":skipped,
        })

    def full_qa_report(self):
        from lsbpipe.photometry.psf_gui_backend import (
            PSF0_DIAGNOSTIC_EXPERIMENTS,
        )
        return {
            "schema":"desidera.psf0-griz-comprehensive-debug/1",
            "created_utc":datetime.now(timezone.utc).isoformat(),
            "project_root":str(self._project_root) if self._project_root else None,
            "science_adopted":False,
            "python":sys.version,
            "platform":platform.platform(),
            "numpy_version":np.__version__,
            "supported_experiments":list(PSF0_DIAGNOSTIC_EXPERIMENTS),
            "batch_status":self.batch_status.text(),
            "saved_dataset":self.source_identity.text(),
            "original_fits_root":self.source_path.text(),
            "selected_source_status":self.source_state.text(),
            "bands":{band:detailed_band_report(panel)
                     for band, panel in self.psf0_panels.items()},
            "no_raw_fits_or_stamp_pixels_in_clipboard":True,
        }

    def _serialize_full_qa(self):
        """Deterministic serializable JSON; never embed raw FITS stamps."""
        def native_json(value):
            if isinstance(value, np.bool_):
                return bool(value)
            if isinstance(value, np.integer):
                return int(value)
            if isinstance(value, np.floating):
                return float(value)
            if isinstance(value, np.ndarray):
                return value.tolist()
            if isinstance(value, Path):
                return str(value)
            raise TypeError(f"Unsupported QA value: {type(value).__name__}")
        return json.dumps(
            self.full_qa_report(), ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False, default=native_json)

    def copy_full_qa(self):
        try:
            content = self._serialize_full_qa()
        except (TypeError, ValueError, OverflowError) as exc:
            self.batch_status.setText(
                f"Full QA report serialization failed: {exc}. Nothing copied.")
            return
        QApplication.clipboard().setText(content)
        self.batch_status.setText(
            f"Copied complete non-adopted PSF-0 griz debug JSON: "
            f"{len(content):,} characters. Paste it into ChatGPT. "
            "No FITS or source pixels copied or modified.")

    def save_full_qa(self):
        """Explicit atomic JSON export, not a scientific PSF product."""
        if self._project_root is None:
            self.batch_status.setText("Open Leo P first; no QA file written.")
            return
        try:
            content = self._serialize_full_qa()
        except (TypeError, ValueError, OverflowError) as exc:
            self.batch_status.setText(
                f"Cannot serialize complete QA: {exc}. No file written.")
            return
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        proposed = self._project_root / (
            f"Desidera_LeoP_PSF0_griz_debug_{suffix}.json")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save non-adopted PSF-0 griz QA", str(proposed),
            "JSON diagnostic report (*.json)")
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".json" or path.is_symlink() or path.exists():
            self.batch_status.setText(
                "Choose a NEW .json filename. Existing files, symlinks "
                "and original FITS will not be overwritten.")
            return
        writer = QSaveFile(str(path))
        if not writer.open(QIODevice.WriteOnly):
            self.batch_status.setText(
                "Cannot open diagnostic JSON for writing: "
                + writer.errorString())
            return
        data = content.encode("utf-8")
        if writer.write(data) != len(data) or not writer.commit():
            writer.cancelWriting()
            self.batch_status.setText(
                "QA JSON export failed. Original FITS unchanged.")
            return
        self.batch_status.setText(
            f"Full non-adopted griz debug JSON saved: {path}. "
            "Attach this .json to ChatGPT; no original FITS modified.")

    def bind_project(self, project):
        """Read the immutable UUID from the existing project manifest."""
        from lsbpipe.surveys.object_projects import read_object_project
        saved = read_object_project(project.root)
        if saved.project_id != project.project_id:
            raise ValueError("Photometry workspace project identity mismatch")
        identity = (
            str(saved.data_root) if saved.data_root is not None else None,
            tuple(saved.selected_dataset[:3]) if saved.selected_dataset else None)
        same_project = (
            self._project_root is not None
            and Path(saved.root) == self._project_root
            and saved.project_id == self._bound_project_id
            and identity == self._bound_source_identity)
        self._project_root = Path(saved.root)
        self._bound_project_id = saved.project_id
        self._bound_source_identity = identity
        # A new UUID, release/region/brick or data root invalidates PSFs.
        # Changing only the saved default band does NOT change the four
        # native per-band input datasets, so existing PSF evidence survives.
        self.refresh(force=not same_project)

    def refresh(self, *, force=False):
        # Only a new project or explicit Refresh can discard a preview.
        # Re-entering the same project/photometry view is non-destructive.
        for panel in self.psf0_panels.values():
            if force and panel._project_root is not None:
                panel._project_root = self._project_root
                panel.refresh_sources()
            else:
                panel.bind_project(self._project_root)
        self.psf0_panel = next(
            (panel for panel in self.psf0_panels.values()
             if panel.run_btn.isEnabled()), self.psf0_panels["g"])
        source_key = (self._bound_project_id, self._bound_source_identity)
        if source_key != self._tab_source_identity:
            # A newly bound project opens on its first usable band.
            # Re-entering an unchanged project preserves the user's
            # currently inspected band and every in-memory PSF result.
            selected_index = self.band_tabs.indexOf(self.psf0_panel)
            self.band_tabs.setCurrentIndex(
                selected_index if selected_index >= 0 else 0)
            self._tab_source_identity = source_key
        self.build_all_psfs_btn.setEnabled(any(
            panel.run_btn.isEnabled() for panel in self.psf0_panels.values()))
        self._update_band_tab_statuses()
        if self._project_root is None:
            self.title.setText("Photometry")
            self.source_state.setText("Open a galaxy project first.")
            self.table.setRowCount(0)
            return
        try:
            result = inspect_photometry_inputs(self._project_root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.source_state.setText(f"Cannot review saved observation data: {exc}")
            self.table.setRowCount(0)
            self.blocker.setText(
                "Input review failed. No FITS or science outputs were changed.")
            return
        self.title.setText(f"Photometry · {result['galaxy']}")
        selected = result["selected_dataset"]
        identity = " / ".join(selected) if selected else "No dataset selected"
        self.source_identity.setText(
            f"Selected native source: {identity}"
            + (" · ICRS target recorded" if result["target_defined"]
               else " · target coordinates not recorded"))
        self.source_path.setText("Original FITS root: " +
                                 (result["data_root"] or "not linked"))
        self.source_state.setText(
            f"{result['status']} · {result['present_count']}/4 files present. "
            f"{result['reason']}")
        self.table.setRowCount(0)
        for product in result["products"]:
            index = self.table.rowCount()
            self.table.insertRow(index)
            for column, value in enumerate((
                    product["kind"].upper(),
                    "File found" if product["present"] else "Missing / unsafe",
                    product["path"])):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.blocker.setText(
            "Science state: NOT_MEASURED_NOT_ADOPTED. PSF-0 remains diagnostic. "
            "The source table checks saved paths only. Building a PSF preview "
            "validates real FITS/WCS and performs an experimental stellar fit, "
            "but PSF wings/spatial representation, galaxy sky, correlated "
            "noise and photometric measurements remain unvalidated.")
