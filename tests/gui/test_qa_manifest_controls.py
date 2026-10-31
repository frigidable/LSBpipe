"""GUI regression: immutable per-run provenance is directly discoverable."""
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWidgets")

from lsbpipe.gui.qa_workbench import ScienceQAControls


def test_manifest_receipt_visible_only_for_current_completed_science_run(qtbot):
    view = ScienceQAControls()
    qtbot.addWidget(view)
    assert not view.open_manifest.isEnabled()
    assert not view.open_report.isEnabled()
    view.show_result({
        "action": "square",
        "summary": "DR9 South · original FITS · 99.8% valid",
        "path": "C:/test/qa-runs/unique/square.json",
        "manifest_path": "C:/test/qa-runs/unique/manifest.json",
        "run_id": "unique",
        "is_figure": False,
    })
    assert view.open_manifest.isEnabled()
    assert view.open_report.isEnabled()
    assert "Run ID: unique" in view.result.toPlainText()
    assert "manifest.json" in view.result.toPlainText()
    view.directory.setText("C:/test/different-pilot")
    assert not view.open_manifest.isEnabled()
    assert not view.open_report.isEnabled()
    assert view._last_manifest_path is None


def test_failed_or_legacy_qa_cannot_open_stale_science_manifest(qtbot):
    view = ScienceQAControls()
    qtbot.addWidget(view)
    view.show_result({
        "action": "figure", "summary": "Earlier result",
        "path": "C:/temp/figure.png",
        "manifest_path": "C:/temp/manifest.json",
        "run_id": "old",
        "is_figure": True,
    })
    view.show_error("Invalid WCS")
    assert not view.open_manifest.isEnabled()
    assert not view.open_report.isEnabled()
    assert "Invalid WCS" in view.result.toPlainText()
    view.show_result({
        "action": "square", "summary": "Unversioned legacy diagnostic",
        "path": "C:/temp/old-square.json", "is_figure": False,
    })
    assert view.open_report.isEnabled()
    assert not view.open_manifest.isEnabled()
    assert "Run ID" not in view.result.toPlainText()
