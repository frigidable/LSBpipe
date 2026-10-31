"""Science GUI adapter: precise local paths, same core, no implicit network."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from lsbpipe.surveys.legacy import BrickHit, SkyField, product_for
from lsbpipe.gui.science_workflows import (
    science_data_root, run_science_qa,
)

FIELD = SkyField(40.096208, 1.229250, 5.)
SOUTH = BrickHit("dr9", "south", "0401p012", 40, 40.2, 1.1, 1.4,
                 {"g": 8, "r": 9, "z": 6})
NORTH = BrickHit("dr9", "north", "0401p012", 40, 40.2, 1.1, 1.4,
                 {"g": 3, "r": 2, "z": 2})


def create_quartet(pilot_root: Path, hit: BrickHit):
    for kind in ("image", "invvar", "nexp", "maskbits"):
        rel = product_for(hit, kind, None if kind == "maskbits" else "r")
        path = pilot_root/"data"/rel.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic placeholder for path verification")
    return pilot_root/"data"


def test_accepts_pilot_parent_or_data_root_and_requires_all_four(tmp_path):
    data = create_quartet(tmp_path, SOUTH)
    assert science_data_root(tmp_path, hit=SOUTH, band="r") == data
    assert science_data_root(data, hit=SOUTH, band="r") == data
    with pytest.raises(FileNotFoundError, match="complete"):
        science_data_root(tmp_path, hit=NORTH, band="r")
    item = product_for(SOUTH, "maskbits", None)
    (data/item.relative_path).unlink()
    with pytest.raises(FileNotFoundError, match="MASKBITS"):
        science_data_root(tmp_path, hit=SOUTH, band="r")


def test_square_workflow_uses_existing_core_and_saves_metadata(tmp_path, monkeypatch):
    from lsbpipe.surveys import square_coverage
    create_quartet(tmp_path, SOUTH)
    calls = []
    report = SimpleNamespace(
        pixel_centers_sampled=1311025,
        polygon_within_single_brick=True,
        instrumentally_usable_fraction=0.9982,
        mean_nexp_where_positive=8.68,
        maskbit_counts={"SATUR_R": 2309, "MEDIUM": 8371, "GALAXY": 14942},
    )
    def inspect(root, *, release, region, brick, band, field):
        calls.append((root, release, region, brick, band, field))
        return report
    def save(res, path):
        path.write_text(json.dumps({"status":"DIAGNOSTIC_NOT_ADOPTED"}))
    monkeypatch.setattr(square_coverage, "inspect_local_square", inspect)
    monkeypatch.setattr(square_coverage, "save_square_coverage", save)
    result = run_science_qa(
        "square", field=FIELD, hit=SOUTH, band="r", directory=tmp_path)
    assert calls == [(tmp_path/"data", "dr9", "south", "0401p012", "r", FIELD)]
    assert "99.820%" in result["summary"]
    assert "2,309" in result["summary"]
    assert result["is_figure"] is False
    assert Path(result["path"]).is_file()
    assert result["provenance"] == ("dr9", "south", "0401p012", "r")
    assert result["status"] == "DIAGNOSTIC_NOT_ADOPTED"


def test_mask_workflow_uses_core_and_keeps_mask_interpretation(tmp_path, monkeypatch):
    from lsbpipe.surveys import mask_localization
    create_quartet(tmp_path, SOUTH)
    expected = {
        "bits": {
            "SATUR_R": {"total_pixels_inside_square": 2309,
                        "connected_regions": 3,
                        "regions": [{"pixels":2163,
                                     "angular_separation_arcsec":166.5}]},
            "MEDIUM": {"total_pixels_inside_square": 8371,
                       "connected_regions": 2, "regions": []},
            "GALAXY": {"total_pixels_inside_square": 14942,
                       "connected_regions": 1, "regions": []},
        }
    }
    monkeypatch.setattr(mask_localization, "inspect_mask_components",
                        lambda *a, **k: expected)
    def save(res, path):
        path.write_text(json.dumps(res))
    monkeypatch.setattr(mask_localization, "save_mask_components", save)
    result = run_science_qa(
        "masks", field=FIELD, hit=SOUTH, band="r", directory=tmp_path)
    assert "SATUR_R: 2,309" in result["summary"]
    assert "SGA" not in result["summary"] or "GALAXY" in result["summary"]
    assert json.loads(Path(result["path"]).read_text()) == expected


def test_four_panel_image_action_returns_preview_path(tmp_path, monkeypatch):
    from lsbpipe.surveys import qa_plots
    create_quartet(tmp_path, SOUTH)
    def fake_render(*, output, **_kwargs):
        Path(output).write_bytes(b"\x89PNG\r\n\x1a\n")
        return Path(output)
    monkeypatch.setattr(qa_plots, "render_qa_figure", fake_render)
    result = run_science_qa(
        "figure", field=FIELD, hit=SOUTH, band="r", directory=tmp_path)
    assert result["is_figure"] is True
    assert "IMAGE / INVVAR / NEXP / MASKBITS" in result["summary"]
    assert Path(result["path"]).read_bytes().startswith(b"\x89PNG")


def test_paired_qa_requires_both_complete_original_quartets(tmp_path, monkeypatch):
    from lsbpipe.surveys import compare_regions
    north = tmp_path/"north"; south = tmp_path/"south"
    create_quartet(north, NORTH)
    create_quartet(south, SOUTH)
    seen = []
    def compare(n, s, *, field, brick, band):
        seen.append((n,s,field,brick,band))
        return {"native_wcs_alignment":"PASSED_0.001_PIX_TIE_POINTS",
                "pixels":1311025,
                "paired_instrumental_validity":{
                    "both":1286594,"north_only":0,"south_only":22122,
                    "neither":2309}}
    monkeypatch.setattr(compare_regions,"compare_original_regions",compare)
    result = run_science_qa(
        "compare", field=FIELD, hit=SOUTH, band="r",
        directory="", north_directory=north,
        south_directory=south/"data")
    assert seen == [(north/"data",south/"data",FIELD,"0401p012","r")]
    assert "South only: 22,122" in result["summary"]
    assert Path(result["path"]).is_file()
    with pytest.raises(FileNotFoundError):
        run_science_qa(
            "compare", field=FIELD, hit=SOUTH, band="r",
            directory="", north_directory=north,
            south_directory=tmp_path/"missing")


def test_invalid_science_context_has_no_side_effects(tmp_path):
    create_quartet(tmp_path,SOUTH)
    with pytest.raises(ValueError, match="Unsupported"):
        run_science_qa(
            "magic", field=FIELD, hit=SOUTH, band="r", directory=tmp_path)
    with pytest.raises(ValueError, match="not in the selected brick"):
        run_science_qa(
            "square", field=FIELD, hit=SOUTH, band="i", directory=tmp_path)
    new_hit = BrickHit(
        "dr11","north","0401p012",40,40.2,1.1,1.4,{"r":2})
    with pytest.raises(ValueError, match="DR9 only"):
        run_science_qa(
            "compare", field=FIELD, hit=new_hit, band="r", directory="")
