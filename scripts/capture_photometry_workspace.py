"""Deterministic offline P1.1 Photometry screenshot and geometry evidence.

Pure GUI visualization: no FITS read, no science calculations, no archive.
Both states use synthetic metadata; never infer scientific readiness.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

from lsbpipe.gui.photometry_workspace import PhotometryWorkspace
from lsbpipe.surveys.object_projects import (
    create_object_project, save_object_project)
from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.dataset_integrity import _expected_paths


def capture(output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # Windows offscreen QPA may start with an empty font database.
    # Borrow installed system fonts for screenshot capture ONLY; never
    # copy or distribute font files as project artifacts.
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    loaded_families = []
    for font_name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        source = fonts_dir / font_name
        if source.is_file():
            index = QFontDatabase.addApplicationFont(str(source))
            if index >= 0:
                loaded_families.extend(
                    QFontDatabase.applicationFontFamilies(index))
    available = set(QFontDatabase.families())
    family = next((name for name in (
        "Segoe UI", "Arial", "Tahoma", "DejaVu Sans") if name in available),
        app.font().family())
    app.setFont(QFont(family, 10))
    font_evidence = {
        "font_family": QFontInfo(app.font()).family(),
        "registered_system_families": loaded_families,
        "n_font_families": len(available),
    }
    if not available:
        raise RuntimeError(
            "Photometry screenshot capture has no renderable font families")
    manifest = []
    sizes = ((1366, 768), (1600, 900), (1920, 1080))
    with tempfile.TemporaryDirectory(prefix="desidera-photo-capture-") as temporary:
        root = Path(temporary)
        os.environ["LSBPIPE_SESSION_DIR"] = str(root / "session")
        project = create_object_project(
            "Synthetic LSB test object", root / "synthetic_galaxy",
            field=SkyField(40.096208, 1.22925, 5.))
        widget = PhotometryWorkspace()
        try:
            for state in ("no_selection", "three_paths_present",
                          "leo_p_r_paths_g_selected"):
                if state == "leo_p_r_paths_g_selected":
                    project = create_object_project(
                        "Leo P", root / "Leo_P",
                        field=SkyField(155.438012, 18.088025, 5.))
                    project.selected_dataset = ("dr11", "south", "1554p180", "g")
                    project.data_root = root / "leo_p_inputs"
                    project.data_root.mkdir(exist_ok=True)
                    save_object_project(project)
                    for path in _expected_paths(
                            project.data_root,
                            ("dr11", "south", "1554p180", "r")).values():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"not a FITS, UI path-only fixture")
                if state == "three_paths_present":
                    project.selected_dataset = ("dr9", "south", "0401p012", "r")
                    project.data_root = root / "inputs"
                    project.data_root.mkdir(exist_ok=True)
                    save_object_project(project)
                    paths = _expected_paths(
                        project.data_root, project.selected_dataset)
                    for kind in ("image", "nexp", "maskbits"):
                        path = paths[kind]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"synthetic non-FITS fixture")
                widget.bind_project(project)
                for width, height in sizes:
                    widget.resize(width, height)
                    widget.show()
                    app.processEvents()
                    actual = widget.size()
                    if actual.width() != width or actual.height() != height:
                        raise RuntimeError(
                            f"Photometry window resized unexpectedly: {actual}")
                    path = output_dir / f"photometry_{state}_{width}x{height}.png"
                    picture = widget.grab()
                    if picture.isNull() or not picture.save(str(path), "PNG"):
                        raise RuntimeError(f"Cannot capture {path}")
                    record = {
                        "path": str(path),
                        "state": state,
                        "fixture": "SYNTHETIC_NON_FITS_METADATA",
                        "source_status": widget.source_state.text(),
                        "scientific_status": "NOT_MEASURED_NOT_ADOPTED",
                        "size_logical": [width, height],
                        "size_image": [picture.width(), picture.height()],
                        "device_pixel_ratio": picture.devicePixelRatio(),
                        "heading_visible": widget.title.isVisible(),
                        "source_table_rows": widget.table.rowCount(),
                        "psf0_available_band": widget.psf0_panel.band.currentData(),
                        "psf0_preview_run_enabled": widget.psf0_panel.run_btn.isEnabled(),
                        "psf0_diagnostic_save_enabled": widget.psf0_panel.save_btn.isEnabled(),
                        "source_panel_geometry": [
                            widget.table.x(), widget.table.y(),
                            widget.table.width(), widget.table.height()],
                        "visual_reviewed": False,
                        "font_evidence": font_evidence,
                    }
                    manifest.append(record)
                    if state == "leo_p_r_paths_g_selected":
                        # One visible optical band tab at a time, even if
                        # only r has an offline native quartet. All four
                        # per-band states persist while the visible tab
                        # switches; no FITS is opened in this capture.
                        from PySide6.QtWidgets import QScrollArea
                        from PySide6.QtCore import QPoint
                        inspector_scroll = next(
                            area for area in widget.findChildren(QScrollArea)
                            if area.widget() is
                            widget.band_tabs.parentWidget())
                        for band, panel in widget.psf0_panels.items():
                            widget.band_tabs.setCurrentWidget(panel)
                            app.processEvents()
                            inner_y = widget.band_tabs.mapTo(
                                inspector_scroll.widget(), QPoint(0, 0)).y()
                            scrollbar = inspector_scroll.verticalScrollBar()
                            scrollbar.setValue(max(0, inner_y-8))
                            app.processEvents()
                            if not panel.isVisible():
                                raise RuntimeError(
                                    f"Selected PSF tab {band} is not visible")
                            if any(other.isVisible() for key, other
                                   in widget.psf0_panels.items()
                                   if key != band):
                                raise RuntimeError(
                                    "Multiple independent PSF bands visible at once")
                            frame = widget.grab()
                            target = output_dir / (
                                f"psf0_fourband_{band}_{width}x{height}.png")
                            if frame.isNull() or not frame.save(str(target),"PNG"):
                                raise RuntimeError(f"Cannot capture {target}")
                            manifest.append({
                                "path":str(target),
                                "state":f"four_band_panel_{band}",
                                "band":band,
                                "selected_band":panel.band.currentData(),
                                "available":panel.run_btn.isEnabled(),
                                "visible_panel":panel.isVisible(),
                                "n_experiment_modes":panel.experiment.count(),
                                "build_all_button":widget.build_all_psfs_btn.text(),
                                "full_qa_button":widget.copy_full_qa_btn.text(),
                                "size_logical":[width,height],
                                "size_image":[frame.width(),frame.height()],
                                "fixture":"SYNTHETIC_NON_FITS_METADATA",
                                "scientific_status":"NOT_MEASURED_NOT_ADOPTED",
                                "visual_reviewed":False,
                            })
            # Explicitly synthetic gallery: no real Leo P image bytes are
            # accessible to CI. Capture equal-square cutout, PSF and radial
            # previews plus manual exclusion controls at practical sizes.
            import numpy as np
            from lsbpipe.gui.psf_star_review import PSFStarReviewDialog
            yy, xx = np.indices((31, 31), dtype=float)
            mock_stars = []
            for i in range(10):
                source = (30. + i) * np.exp(
                    -.5*((xx-15.1)**2+(yy-14.8)**2)/5.3)
                model = source*.985
                model[-3:, -3:] = -.004
                mock_stars.append({
                    "star_id":f"star-{i+1:04d}", "role":"train",
                    "x_pix":300. + i*40., "y_pix":100. + i*50.,
                    "ra_deg":155.438012, "dec_deg":18.088025,
                    "cutout":source, "model":model,
                    "valid":np.ones(source.shape,dtype=bool),
                    "center_in_cutout":(15.1,14.8),
                    "peak_nMgy":float(source.max()),
                    "sky_nMgy":.001*i, "gradient_sigma_span":.3,
                })
            gallery = PSFStarReviewDialog({
                "schema":"desidera.psf0-star-review-in-memory/1",
                "status":"RESEARCHER_REVIEW_NOT_SCIENCE_ADOPTED",
                "min_train_stars":4,
                "guessed_fwhm_pix":4.8,
                "excluded_training_star_ids":(),
                "stars":mock_stars,
            })
            try:
                gallery._controls["star-0003"].setChecked(True)
                for width, height in ((1100, 760), (1400, 900)):
                    gallery.resize(width, height)
                    gallery.show()
                    app.processEvents()
                    actual = gallery.size()
                    path = output_dir / f"psf0_star_review_SYNTHETIC_{width}x{height}.png"
                    picture = gallery.grab()
                    if picture.isNull() or not picture.save(str(path), "PNG"):
                        raise RuntimeError(f"Cannot capture {path}")
                    manifest.append({
                        "path":str(path),
                        "state":"synthetic_psf_training_mosaic",
                        "fixture":"SYNTHETIC_STAR_ARRAYS_NO_LEO_P_FITS",
                        "scientific_status":"NOT_MEASURED_NOT_ADOPTED",
                        "size_logical":[width,height],
                        "size_image":[picture.width(),picture.height()],
                        "star_cards":len(mock_stars),
                        "excluded_training_star_ids":list(gallery.excluded_ids()),
                        "source_fits_read":False,
                        "visual_reviewed":False,
                    })
            finally:
                gallery.close()
                gallery.deleteLater()
            widget.hide()
        finally:
            widget.close()
            widget.deleteLater()
    destination = output_dir / "photometry_capture_manifest.json"
    destination.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    records = capture(args.output_dir)
    print(f"Captured {len(records)} non-scientific photometry screenshots.")
