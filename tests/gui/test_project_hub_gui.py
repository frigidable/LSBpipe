"""Real Qt checks for the offline object-centric launcher."""
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from PySide6.QtWidgets import QDialog
from scripts.offline_gui_fixture import offline_legacy_viewer
from lsbpipe.gui.project_hub import ProjectHub, CreateProjectDialog
from lsbpipe.surveys.object_projects import (
    create_object_project, read_object_project, recent_projects,
)
from lsbpipe.surveys.legacy import SkyField


@pytest.fixture
def hub(qtbot):
    with offline_legacy_viewer():
        window = ProjectHub()
    qtbot.addWidget(window)
    window.show()
    return window


def test_launcher_is_empty_and_never_opens_viewer_on_first_launch(hub):
    assert hub.objectName() == "lsbpipe_project_hub"
    assert hub.projects.count() == 0
    assert hub.workspace is None
    assert not hub.continue_btn.isEnabled()
    assert hub.new_btn.isEnabled()
    assert hub.import_btn.isEnabled()


def test_three_stage_wizard_creates_project_without_fits_or_coordinates(
        qtbot, tmp_path):
    dialog = CreateProjectDialog(default_root=tmp_path)
    qtbot.addWidget(dialog)
    dialog.name.setText("Unknown LSB dwarf")
    assert dialog.pages.currentIndex() == 0
    assert "Unknown_LSB_dwarf" in dialog.directory.text()
    dialog._advance()
    assert dialog.pages.currentIndex() == 1
    dialog._advance()
    assert dialog.pages.currentIndex() == 2
    assert "not specified" in dialog.description.text()
    dialog._advance()
    assert dialog.result() == QDialog.Accepted
    project = dialog.project
    assert project is not None and project.field is None
    assert not (project.root / "data").exists()
    assert read_object_project(project.root).project_id == project.project_id


def test_hub_open_create_and_return_preserves_object_identity(
        hub, qtbot, tmp_path):
    project = create_object_project(
        "UGC 2162", tmp_path / "UGC_2162",
        field=SkyField(40.096208, 1.22925, 5.))
    with offline_legacy_viewer():
        hub.refresh()
        assert hub.projects.count() == 1
        hub._open_selected()
    window = hub.workspace
    assert window is not None
    assert window._object_project.project_id == project.project_id
    assert window._local_project is None
    assert window.ra.value() == pytest.approx(40.096208)
    assert window.dec.value() == pytest.approx(1.22925)
    assert window.bricks.rowCount() == 0
    assert window.back_to_projects.isVisible()
    assert not hub.isVisible()
    window._go_back_to_projects()
    assert hub.workspace is None
    assert hub.isVisible()
    assert read_object_project(project.root).project_id == project.project_id
    hub.refresh()
    assert hub.projects.count() == 1


def test_unresolved_draft_has_no_fake_science_target(hub, tmp_path):
    project = create_object_project("NewTarget", tmp_path / "draft")
    with offline_legacy_viewer():
        hub._enter_project(project)
    w = hub.workspace
    assert w.ra.text() == ""
    assert w.dec.text() == ""
    assert w.bricks.rowCount() == 0
    assert w.manifest is None
    assert "No ICRS position" in w.target_hint.text()
    w._go_back_to_projects()
    assert read_object_project(project.root).field is None


def test_filters_and_missing_project_stays_in_library(hub, tmp_path):
    create_object_project("NGC 2146 satellite", tmp_path / "LSB1")
    create_object_project("UGC 2162", tmp_path / "LSB2")
    hub.refresh()
    assert hub.projects.count() == 2
    hub.filter.setText("2162")
    assert sum(not hub.projects.item(i).isHidden()
               for i in range(hub.projects.count())) == 1
    hub.filter.clear()
    root = tmp_path / "LSB2"
    root.rename(tmp_path / "moved")
    hub.refresh()
    assert any("Missing" in hub.projects.item(i).text()
               for i in range(hub.projects.count()))


def test_import_legacy_folder_into_hub_without_network(hub, tmp_path, monkeypatch):
    from lsbpipe.surveys.legacy import BrickHit, product_for
    import json
    root = tmp_path / "UGC_2162"
    data = root / "data"
    hit = BrickHit("dr9", "south", "0401p012", 0, 0, 0, 0, {"r": 1})
    for kind in ("image", "invvar", "nexp", "maskbits"):
        p = product_for(hit, kind, None if kind == "maskbits" else "r")
        path = data / p.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing FITS placeholder")
    (data / "download-report.json").write_text(json.dumps({
        "schema": "lsbpipe.legacy-download-report/0.1",
        "field": {"ra_deg": 40.096208, "dec_deg": 1.22925,
                  "width_arcmin": 5.}, "results": []}))
    def forbid(*args, **kwargs):
        raise AssertionError("No network request permitted")
    monkeypatch.setattr(
        "lsbpipe.gui.survey_explorer.discover_bricks", forbid)
    with offline_legacy_viewer():
        assert hub.import_folder(root)
    assert hub.workspace is not None
    assert hub.workspace._local_project is not None
    assert hub.workspace.bricks.rowCount() == 1
    assert hub.workspace._object_project.display_name == "UGC_2162"
    hub.workspace._go_back_to_projects()
    assert len(recent_projects()) == 1
