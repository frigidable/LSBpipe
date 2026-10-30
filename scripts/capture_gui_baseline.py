"""Offline Qt screenshot baseline for the *existing* Survey Explorer UI.

Captures geometry and layout only. The remote Legacy Viewer is deliberately
NOT loaded and is never represented as real survey imagery.
Requires: pip install -e ".[gui]" (Windows CI sets QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from offline_gui_fixture import offline_legacy_viewer, apply_offline_state

# Intentional: this is a deterministic noninteractive CI capture, NOT a
# replacement for real Windows/on-device Qt WebEngine validation.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontInfo, QFontDatabase
from PySide6.QtWidgets import QApplication, QGroupBox, QTableWidget

from lsbpipe.gui.survey_explorer import SurveyExplorer


SIZES = ((1366, 768), (1600, 900), (1920, 1080))


def _bounds(widget, root):
    pos = widget.mapTo(root, QPoint(0, 0))
    rect = widget.rect()
    return {
        "x": pos.x(), "y": pos.y(),
        "width": rect.width(), "height": rect.height(),
        "visible": bool(widget.isVisible()),
    }


def _capture(window, app, outdir: Path, width: int, height: int, state: str):
    window.resize(width, height)
    window.show()
    for _ in range(8):
        app.processEvents()

    screenshot = outdir / f"survey_{state}_{width}x{height}.png"
    pixmap = window.grab()
    if pixmap.isNull() or not pixmap.save(str(screenshot), "PNG"):
        raise RuntimeError(f"Unable to capture Qt screenshot: {screenshot}")
    if screenshot.stat().st_size < 1024:
        raise RuntimeError(f"Screenshot appears truncated: {screenshot}")

    splitter = window.centralWidget()
    if splitter is None:
        raise RuntimeError("Survey Explorer has no central QWidget")
    widgets = {
        "search_button": window.search_btn,
        "target_form": window.findChild(QGroupBox),
        "brick_table": window.bricks,
        "file_table": window.files,
        "coverage_button": window.qa_btn,
        "download_button": window.download_btn,
        "web_viewer_placeholder": window.sky,
    }
    report = {
        "state": state + "_offline_synthetic",
        "capture_font_family": QFontInfo(app.font()).family(),
        "font_catalog_families": QFontDatabase.families()[:40],
        "size_requested_logical": [width, height],
        "screenshot": screenshot.name,
        "screenshot_px": [pixmap.width(), pixmap.height()],
        "screenshot_bytes": screenshot.stat().st_size,
        "github_sha": os.getenv("GITHUB_SHA"),
        "network_used": False,
        "viewer_content": "BLANK_OFFLINE_PLACEHOLDER_NOT_SURVEY_IMAGERY",
        "scientific_status": "NO_SCIENCE_RESULT",
        "splitter": _bounds(splitter, window),
        "regions": {
            "control_panel": _bounds(splitter.widget(0), window),
            "viewer": _bounds(splitter.widget(1), window),
        },
        "widgets": {
            name: _bounds(widget, window)
            for name, widget in widgets.items() if widget is not None
        },
        "group_boxes": [
            {"title": box.title(), **_bounds(box, window)}
            for box in window.findChildren(QGroupBox)
        ],
        "tables": [
            {"columns": table.columnCount(), "rows": table.rowCount(),
             **_bounds(table, window)}
            for table in window.findChildren(QTableWidget)
        ],
    }
    (outdir / f"survey_{state}_{width}x{height}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="gui-artifacts")
    args = parser.parse_args()
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    # Prefer known installed UI fonts, and record which Qt actually resolved.
    # Offscreen rasterization still requires visual inspection.
    fonts = set(QFontDatabase.families())
    preferred = next((name for name in ("Segoe UI", "Arial", "DejaVu Sans")
                      if name in fonts), app.font().family())
    app.setFont(QFont(preferred, 10))
    # No Viewer navigation or archive activity from this controlled
    # capture process. The application source itself is NOT modified.
    reports = []
    # Fresh widget instance for every state: no inadvertent selection/resize
    # carryover between screenshots, but all components are real Qt widgets.
    with offline_legacy_viewer():
        for state in ("S0", "S1", "S2", "S3"):
            window = SurveyExplorer()
            try:
                apply_offline_state(window, state)
                for w, h in SIZES:
                    reports.append(_capture(
                        window, app, outdir, w, h, state))
            finally:
                window.close()
                app.processEvents()
    index = {
        "evidence_level": "E1_CAPTURED_NOT_VISUALLY_REVIEWED",
        "screens": reports,
        "limitations": [
            "WebEngine intentionally replaced by a blank local offline placeholder",
            "All catalog and product S1–S3 data are synthetic fixtures",
            "Offscreen Qt capture cannot verify real monitor / DPI rendering",
            "This job does not certify accessibility or scientific validity",
        ],
    }
    (outdir / "manifest.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Offline GUI capture: {len(reports)} screenshots in {outdir}")
    for report in reports:
        print(report["screenshot"], report["screenshot_bytes"], "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
