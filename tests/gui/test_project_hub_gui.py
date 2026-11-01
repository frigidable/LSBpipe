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


def test_object_passport_wins_over_stale_legacy_on_gui_reopen(
        hub, qtbot, tmp_path):
    """Do not replace a revised galaxy center with old download-report ICRS."""
    import json
    from lsbpipe.surveys.legacy import BrickHit, product_for
    from lsbpipe.surveys.object_projects import (
        import_object_project, save_object_project)
    root = tmp_path / "UGC_2162"
    data = root / "data"
    for region in ("north", "south"):
        hit = BrickHit("dr9", region, "0401p012", 0, 0, 0, 0, {"r": 1})
        for kind in ("image", "invvar", "nexp", "maskbits"):
            p = product_for(
                hit, kind, None if kind == "maskbits" else "r")
            filename = data / p.relative_path
            filename.parent.mkdir(parents=True, exist_ok=True)
            filename.write_bytes(b"unaltered original FITS placeholder")
    legacy = root / "lsbpipe-project.json"
    legacy.write_text(json.dumps({
        "schema": "lsbpipe.local-project/0.1",
        "field": {"ra_deg": 40.096208, "dec_deg": 1.22925,
                  "width_arcmin": 5.},
        "selected": ["dr9", "north", "0401p012", "r"],
    }), encoding="utf-8")
    old_bytes = legacy.read_bytes()
    project = import_object_project(root)
    revised = SkyField(40.101234, 1.225678, 6.)
    project.field = revised
    project.selected_dataset = ("dr9", "south", "0401p012", "r")
    project.target_radius_arcsec = 35.
    project.sky_inner_arcsec = 65.
    project.sky_outer_arcsec = 110.
    save_object_project(project)
    with offline_legacy_viewer():
        hub._enter_project(project)
    w = hub.workspace
    assert w is not None and w._local_project is not None
    assert w.ra.value() == pytest.approx(revised.ra_deg)
    assert w.dec.value() == pytest.approx(revised.dec_deg)
    assert w.field_width.value() == pytest.approx(6.)
    assert w._local_project.field == revised
    assert w._local_project.selected == project.selected_dataset
    assert w.hits[w.bricks.currentRow()].region == "south"
    assert (w.target_radius.value(), w.sky_inner.value(),
            w.sky_outer.value()) == (35., 65., 110.)
    assert legacy.read_bytes() == old_bytes

    # Editing the ICRS center never updates the original/legacy files.
    w.ra.setText("40.111111")
    w.target_radius.setValue(40.)
    w._go_back_to_projects()
    assert hub.workspace is None
    current = read_object_project(root)
    assert current.field.ra_deg == pytest.approx(40.111111)
    assert current.field.dec_deg == pytest.approx(revised.dec_deg)
    assert current.selected_dataset == ("dr9", "south", "0401p012", "r")
    assert current.target_radius_arcsec == pytest.approx(40.)
    assert legacy.read_bytes() == old_bytes

    # Closing/reopening must not re-import the original download position.
    with offline_legacy_viewer():
        hub._enter_project(current)
    reopened = hub.workspace
    assert reopened.ra.value() == pytest.approx(40.111111)
    assert reopened.dec.value() == pytest.approx(revised.dec_deg)
    assert reopened._local_project.field.ra_deg == pytest.approx(40.111111)
    assert reopened.hits[reopened.bricks.currentRow()].region == "south"
    assert legacy.read_bytes() == old_bytes
    reopened._go_back_to_projects()


def test_empty_galaxy_qa_radii_are_persisted_without_fits(hub, tmp_path):
    project = create_object_project("Unobserved dwarf", tmp_path / "draft")
    with offline_legacy_viewer():
        hub._enter_project(project)
    w = hub.workspace
    w.target_radius.setValue(35.)
    w.sky_inner.setValue(65.)
    w.sky_outer.setValue(110.)
    w._go_back_to_projects()
    again = read_object_project(project.root)
    assert (again.target_radius_arcsec, again.sky_inner_arcsec,
            again.sky_outer_arcsec) == (35., 65., 110.)
    with offline_legacy_viewer():
        hub._enter_project(again)
    reopened = hub.workspace
    assert (reopened.target_radius.value(), reopened.sky_inner.value(),
            reopened.sky_outer.value()) == (35., 65., 110.)
    reopened._go_back_to_projects()


def test_missing_saved_dataset_requires_explicit_new_selection(
        hub, tmp_path):
    import json
    from lsbpipe.surveys.legacy import BrickHit, product_for
    from lsbpipe.surveys.object_projects import (
        import_object_project, save_object_project)
    root = tmp_path / "UGC_2162"
    data = root / "data"
    hit = BrickHit("dr9", "south", "0401p012", 0, 0, 0, 0, {"r": 1})
    for kind in ("image", "invvar", "nexp", "maskbits"):
        product = product_for(
            hit, kind, None if kind == "maskbits" else "r")
        path = data / product.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"read only")
    (data / "download-report.json").write_text(json.dumps({
        "schema": "lsbpipe.legacy-download-report/0.1",
        "field": {"ra_deg": 40.096208, "dec_deg": 1.22925,
                  "width_arcmin": 5.0},
    }), encoding="utf-8")
    project = import_object_project(root)
    missing = ("dr9", "north", "0401p012", "r")
    project.selected_dataset = missing
    save_object_project(project)
    with offline_legacy_viewer():
        hub._enter_project(project)
    w = hub.workspace
    assert w is not None
    assert w.bricks.rowCount() == 1
    assert not w.bricks.selectedItems()
    assert "Saved dataset missing" in w.project_status.text()
    w._go_back_to_projects()
    assert read_object_project(root).selected_dataset == missing

    # Only deliberate row selection changes the object passport.
    with offline_legacy_viewer():
        hub._enter_project(read_object_project(root))
    w = hub.workspace
    w.bricks.selectRow(0)
    w._go_back_to_projects()
    assert read_object_project(root).selected_dataset == (
        "dr9", "south", "0401p012", "r")
