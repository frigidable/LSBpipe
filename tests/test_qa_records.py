"""P0 regression tests: immutable science inputs and non-overwriting QA runs."""
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from lsbpipe.gui.qa_records import (
    ScienceContext, new_qa_run, original_quartet, snapshot_inputs,
)
from lsbpipe.gui.science_workflows import run_science_qa
from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for


FIELD = SkyField(40.096208, 1.22925, 5.)
HIT = BrickHit("dr9", "south", "0401p012", 40., 40.2, 1.1, 1.4,
               {"r": 9})


def inputs(tmp_path):
    data_root = tmp_path / "pilot" / "data"
    data_root.mkdir(parents=True)
    for i, kind in enumerate(("image", "invvar", "nexp", "maskbits")):
        rel = product_for(HIT, kind, None if kind == "maskbits" else "r")
        file = data_root / rel.relative_path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(bytes([i + 1]) * (10 + i))
    return data_root


def ctx(data, *, action="masks", r=35., inner=65., outer=110.):
    return ScienceContext.capture(
        action=action, field=FIELD, hit=HIT, band="r",
        directory=data.parent, target_radius_arcsec=r,
        sky_inner_arcsec=inner, sky_outer_arcsec=outer)


def test_context_immutable_and_sensitive_to_full_user_science_context(tmp_path):
    data = inputs(tmp_path)
    first = ctx(data)
    with pytest.raises(FrozenInstanceError):
        first.band = "g"
    assert ctx(data, r=30.) != first
    assert ctx(data, inner=70.) != first
    assert ctx(data, outer=119.) != first
    assert ctx(data, action="figure") != first
    assert ScienceContext.capture(
        action="masks", field=SkyField(40.096209,1.22925,5.),
        hit=HIT, band="r", directory=data.parent) != first
    assert ScienceContext.capture(
        action="masks", field=SkyField(40.096208,1.22925,6.),
        hit=HIT, band="r", directory=data.parent) != first
    assert ScienceContext.capture(
        action="masks", field=FIELD, hit=HIT, band="r",
        directory=tmp_path/"different") != first
    # Accept equivalent expressions of the same *absolute* data directory.
    assert ScienceContext.capture(
        action="masks", field=FIELD, hit=HIT, band="r",
        directory=data.parent/"." ) == first


def test_atomic_records_never_overwrite_previous_runs(tmp_path):
    data = inputs(tmp_path)
    original = original_quartet(data, HIT, "r")
    before = {k:v.read_bytes() for k,v in original.items()}
    fingerprints = snapshot_inputs({"source": original})
    expected_sha = sha256(before["image"]).hexdigest()
    first = ctx(data)
    for idx in range(2):
        with new_qa_run(data_root=data, context=first,
                        inputs=fingerprints) as (stage, record):
            (stage / "report.json").write_text(
                json.dumps({"measurement": idx}), encoding="utf-8")
        final = data.parent / "qa-runs" / record["run_id"]
        assert final.is_dir()
        manifest = json.loads((final / "manifest.json").read_text("utf-8"))
        assert manifest["science_status"] == "DIAGNOSTIC_NOT_ADOPTED"
        assert manifest["input_original_fits"]["source"]["image"][
            "sha256_local"] == expected_sha
        assert manifest["context"]["target_radius_arcsec"] == 35.
        assert manifest["artifacts"]["report.json"]["bytes"] > 0
        assert json.loads((final/"report.json").read_text())["measurement"] == idx
        if idx == 0:
            old = final
        else:
            assert old != final
            assert json.loads((old / "report.json").read_text())["measurement"] == 0
    assert not any((data.parent/"qa-runs").glob("*.partial"))
    for kind, path in original.items():
        assert path.read_bytes() == before[kind]
    assert not (data / "qa-runs").exists()


def test_partial_failure_cleans_staging_but_keeps_prior_result(tmp_path):
    data = inputs(tmp_path)
    quartet = {"source": original_quartet(data, HIT, "r")}
    snap = snapshot_inputs(quartet)
    with new_qa_run(data_root=data, context=ctx(data), inputs=snap) as (stage, record):
        (stage / "result.json").write_text("valid", encoding="utf-8")
    saved = data.parent / "qa-runs" / record["run_id"]
    with pytest.raises(ValueError, match="Synthetic QA error"):
        with new_qa_run(data_root=data, context=ctx(data), inputs=snap) as (stage, _):
            (stage / "result.json").write_text("partial", encoding="utf-8")
            raise ValueError("Synthetic QA error")
    assert (saved / "result.json").read_text() == "valid"
    assert len(list((data.parent/"qa-runs").iterdir())) == 1


def test_input_mutation_before_atomic_commit_rejected(tmp_path):
    data = inputs(tmp_path)
    files = original_quartet(data, HIT, "r")
    snap = snapshot_inputs({"source": files})
    with pytest.raises(RuntimeError, match="changed"):
        with new_qa_run(data_root=data, context=ctx(data), inputs=snap) as (stage, _):
            (stage / "result.json").write_text("untrusted")
            files["image"].write_bytes(b"changed content, do not publish")
    assert list((data.parent/"qa-runs").iterdir()) == []


def test_science_action_uses_distinct_records_for_different_apertures(
        tmp_path, monkeypatch):
    from lsbpipe.surveys import mask_localization
    data = inputs(tmp_path)
    observed = []
    def fake_inspect(directory, **kwargs):
        observed.append((directory, kwargs["target_radius_arcsec"],
                         kwargs["sky_inner_arcsec"]))
        return {
            "bits": {
                name: {"total_pixels_inside_square": 1,
                       "connected_regions":1, "regions":[]}
                for name in ("SATUR_R","MEDIUM","GALAXY")
            }}
    monkeypatch.setattr(mask_localization, "inspect_mask_components", fake_inspect)
    monkeypatch.setattr(mask_localization, "save_mask_components",
                        lambda result, path: path.write_text(json.dumps(result)))
    first = run_science_qa(
        "masks", field=FIELD, hit=HIT, band="r",
        directory=data.parent, target_radius_arcsec=35.)
    second = run_science_qa(
        "masks", field=FIELD, hit=HIT, band="r",
        directory=data.parent, target_radius_arcsec=40.)
    assert first["path"] != second["path"]
    assert first["run_id"] != second["run_id"]
    assert Path(first["path"]).exists()
    assert Path(second["path"]).exists()
    assert first["manifest_path"] != second["manifest_path"]
    m1 = json.loads(Path(first["manifest_path"]).read_text())
    m2 = json.loads(Path(second["manifest_path"]).read_text())
    assert m1["context"]["target_radius_arcsec"] == 35
    assert m2["context"]["target_radius_arcsec"] == 40
    assert len(observed) == 2
    assert not (data / "qa-reports").exists()


def test_mismatched_submitted_context_rejected_before_any_science_work(tmp_path):
    data = inputs(tmp_path)
    with pytest.raises(ValueError, match="context"):
        run_science_qa(
            "masks", field=FIELD, hit=HIT, band="r",
            directory=data.parent, context=ctx(data, r=22.))
    assert not (data.parent/"qa-runs").exists()



def test_fits_mutated_with_same_size_and_preserved_mtime_is_rejected(tmp_path):
    """Size and mtime alone do not establish original FITS immutability."""
    import os
    data = inputs(tmp_path)
    originals = original_quartet(data, HIT, "r")
    target = originals["image"]
    before = target.stat()
    snap = snapshot_inputs({"source": originals})
    with pytest.raises(RuntimeError, match="changed"):
        with new_qa_run(
                data_root=data, context=ctx(data), inputs=snap) as (stage, _):
            (stage / "result.json").write_text("untrusted", encoding="utf-8")
            payload = target.read_bytes()
            target.write_bytes(b"X" * len(payload))
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not list((data.parent / "qa-runs").iterdir())
