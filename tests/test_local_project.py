"""Offline-first project recovery: original path inventory, metadata and safety."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for
from lsbpipe.surveys.local_project import (
    PROJECT_FILE, last_project_directory, open_local_project,
    save_local_project, session_pointer_path)


FIELD = SkyField(40.096208, 1.229250, 5.)


def make_data(root: Path, *, data_subdir=True, with_report=True,
              both_regions=True, partial_g=False):
    data = root / "data" if data_subdir else root
    for region in ("north", "south") if both_regions else ("south",):
        hit = BrickHit("dr9", region, "0401p012", 0, 0, 0, 0, {"r": 1, "g": 1})
        for kind in ("image", "invvar", "nexp", "maskbits"):
            rel = product_for(hit, kind, None if kind == "maskbits" else "r")
            file = data / rel.relative_path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"LOCAL ORIGINAL PLACEHOLDER " + kind.encode())
        if partial_g:
            rel = product_for(hit, "image", "g")
            (data / rel.relative_path).write_bytes(b"image, other bands absent")
    if with_report:
        (data / "download-report.json").write_text(json.dumps({
            "schema": "lsbpipe.legacy-download-report/0.1",
            "field": {"ra_deg": FIELD.ra_deg, "dec_deg": FIELD.dec_deg,
                      "width_arcmin": FIELD.width_arcmin},
            "results": []}), encoding="utf-8")
    return data


def test_existing_ugc_folder_with_data_subdirectory_recovers_without_download(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "user-config"))
    project_root = tmp_path / "UGC_2162"
    data = make_data(project_root, partial_g=True)
    before = {p: p.read_bytes() for p in data.rglob("*.fits.fz")}
    project = open_local_project(project_root)
    assert project.field == FIELD
    assert project.field_source == "download-report"
    assert project.root == project_root
    assert project.data_root == data
    assert len(project.hits) == 2
    assert project.selected[2:] == ("0401p012", "r")
    assert len(project.complete) == 4
    assert sum(project.complete.values()) == 2
    assert all(project.complete[( "dr9", r, "0401p012", "r")]
               for r in ("north", "south"))
    assert not any(project.complete[( "dr9", r, "0401p012", "g")]
                   for r in ("north", "south"))
    save_local_project(project)
    assert last_project_directory() == project_root
    assert session_pointer_path().is_file()
    assert (project_root / PROJECT_FILE).is_file()
    reopened = open_local_project(data)
    assert reopened.field == FIELD
    assert reopened.complete == project.complete
    assert reopened.root == project_root
    for path, old in before.items():
        assert path.read_bytes() == old


def test_roundtrip_target_selection_apertures_and_result_index(tmp_path, monkeypatch):
    monkeypatch.setenv("LSBPIPE_SESSION_DIR", str(tmp_path / "settings"))
    root = tmp_path / "galaxy"
    make_data(root)
    project = open_local_project(root)
    project.selected = ("dr9", "south", "0401p012", "r")
    project.target_radius_arcsec = 35
    project.sky_inner_arcsec = 65
    project.sky_outer_arcsec = 110
    project.last_science = {"action": "census", "path": "synthetic-qa.json"}
    save_local_project(project)
    restored = open_local_project(root)
    assert restored.selected == project.selected
    assert (restored.target_radius_arcsec,
            restored.sky_inner_arcsec,
            restored.sky_outer_arcsec) == (35, 65, 110)
    assert restored.last_science == project.last_science
    assert (root / PROJECT_FILE).read_text(encoding="utf-8").count(
        "LOCAL_FILES_PRESENT_NOT_SCIENTIFICALLY_ADOPTED") == 1


def test_unrecorded_target_requires_explicit_user_coordinates(tmp_path):
    root = tmp_path / "data-only"
    make_data(root, with_report=False, data_subdir=False, both_regions=False)
    with pytest.raises(ValueError, match="No saved target coordinates"):
        open_local_project(root)
    imported = open_local_project(root, fallback_field=FIELD)
    assert imported.field == FIELD
    assert imported.field_source == "explicit_gui_target"


def test_missing_project_and_foreign_schema_fail_closed(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        open_local_project(tmp_path / "does-not-exist")
    root = tmp_path / "actual"
    make_data(root)
    (root / PROJECT_FILE).write_text(
        json.dumps({"schema": "other.project/1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown.*schema"):
        open_local_project(root)
    assert json.loads((root / PROJECT_FILE).read_text()) == {
        "schema": "other.project/1"}


def test_partial_quadruplet_remains_partial_not_silently_complete(tmp_path):
    root = tmp_path / "pilot"
    data = make_data(root, both_regions=False)
    hit = BrickHit("dr9", "south", "0401p012", 0, 0, 0, 0, {"r": 1})
    missing = data / product_for(hit, "nexp", "r").relative_path
    missing.unlink()
    result = open_local_project(root)
    assert result.complete[("dr9", "south", "0401p012", "r")] is False
    assert result.selected is not None
    assert len(result.on_disk) == 3


def test_import_older_qa_record_by_exact_context_not_arbitrary_latest(tmp_path):
    root = tmp_path / "UGC_2162"
    data = make_data(root, both_regions=False)
    qa = data.parent / "qa-runs" / "20261031-example"
    qa.mkdir(parents=True)
    receipt = {
        "schema": "lsbpipe.gui-science-qa/1",
        "context": {
            "release": "dr9", "region": "south",
            "brick": "0401p012", "band": "r", "action": "figure",
            "ra_deg": FIELD.ra_deg, "dec_deg": FIELD.dec_deg,
            "width_arcmin": FIELD.width_arcmin}}
    (qa / "manifest.json").write_text(json.dumps(receipt))
    (qa / "four-layers.png").write_bytes(b"synthetic")
    result = open_local_project(root)
    assert result.last_science["action"] == "figure"
    assert result.last_science["path"] == str(qa / "four-layers.png")
    receipt["context"]["ra_deg"] = 31.
    (qa / "manifest.json").write_text(json.dumps(receipt))
    assert open_local_project(root).last_science == {}
