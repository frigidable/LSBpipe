"""Offline Qt acceptance: visible photometry workflow follows PSF-first stages."""
from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QGroupBox
from lsbpipe.gui.photometry_workspace import PhotometryWorkspace, _STAGES


def test_psf_first_science_workflow_has_correct_stage_order(qtbot):
    w = PhotometryWorkspace()
    qtbot.addWidget(w)
    w.resize(1366, 768)
    w.show()
    names = [heading.lower() for _, heading, _ in _STAGES]
    assert len(names) == 8
    assert names == [
        "data preparation", "scene initialization",
        "preliminary sky", "psf reconstruction",
        "scene & smart masks", "sky reconstruction",
        "refinement & qa", "galaxy photometry",
    ]
    groups = [w.findChild(QGroupBox, f"photometry_stage_{i:02d}")
              for i in range(1, 9)]
    assert all(group is not None for group in groups)
    assert "04 · PSF reconstruction" == groups[3].title()
    assert "05 · Scene & smart masks" == groups[4].title()
    assert "07 · Refinement & QA" == groups[6].title()
    assert "no adopted PSF, sky or galaxy photometry" in w.subtitle.text()
    assert w.table.rowCount() == 0
    assert w._project_root is None
