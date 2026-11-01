"""Offline, no-download acceptance tests for P0.2 dataset registry and footprint."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

pytest.importorskip("astropy")
from astropy.io import fits
from astropy.wcs import WCS

from lsbpipe.survey import LEGACY_BITS
from lsbpipe.surveys.dataset_integrity import (
    SCHEMA, audit_dataset_root, discover_dataset_inventory,
    save_dataset_audit, verify_dataset,
)
from lsbpipe.surveys.legacy import BrickHit, SkyField, product_for

FIELD = SkyField(40.096208, 1.22925, 1.0)


def _make_brick(root, brick, *, band="r", region="south", release="dr9",
                shift_arcsec=0, corrupt=None, shifted_wcs=None, satur=False):
    shape = (70, 60)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [30.5, 35.5]
    wcs.wcs.cdelt = [-1. / 3600, 1. / 3600]
    wcs.wcs.crval = [FIELD.ra_deg + shift_arcsec/3600,
                     FIELD.dec_deg]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    hit = BrickHit(release, region, brick, 0, 0, 0, 0, {band: 1})
    arrays = {
        "image": np.ones(shape, dtype=np.float32),
        "invvar": np.full(shape, 3, dtype=np.float32),
        "nexp": np.full(shape, 2, dtype=np.int16),
        "maskbits": np.zeros(shape, dtype=np.int16),
    }
    if satur:
        arrays["maskbits"][20:25, 20:25] = np.int16(
            1 << LEGACY_BITS["SATUR_R"])
    paths = {}
    for kind, array in arrays.items():
        item = product_for(
            hit, kind, None if kind == "maskbits" else band)
        path = root / item.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        hdr = wcs.to_header()
        if kind == "image":
            hdr["BUNIT"] = "nanomaggies"
        if kind == shifted_wcs:
            hdr["CRVAL1"] += 0.001
        if kind == "maskbits":
            fits.HDUList([fits.PrimaryHDU(),
                          fits.ImageHDU(data=array, header=hdr)]).writeto(path)
        else:
            fits.PrimaryHDU(data=array, header=hdr).writeto(path)
        if kind == corrupt:
            path.write_bytes(b"not a FITS file")
        paths[kind] = path
    return paths


def test_inventory_scans_all_four_products_without_original_image(tmp_path):
    root = tmp_path / "data"
    paths = _make_brick(root, "0401p012", region="south")
    paths["image"].unlink()
    rows = discover_dataset_inventory(root)
    assert len(rows) == 1
    item = rows[0]
    assert item["dataset_id"] == "dr9/south/0401p012/r"
    assert item["status"] == "INCOMPLETE_FILES"
    assert item["products"]["image"]["presence"] == "MISSING"
    assert item["products"]["invvar"]["presence"] == "PRESENT_UNVERIFIED"
    assert audit_dataset_root(root, field=FIELD)["counts"]["validated"] == 0


def test_both_regions_and_multiple_bricks_are_distinct(tmp_path):
    root = tmp_path / "data"
    _make_brick(root, "0401p012", region="north", shift_arcsec=20)
    _make_brick(root, "0401p012", region="south", shift_arcsec=20)
    _make_brick(root, "0400p012", region="south", shift_arcsec=-20)
    files_before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob("*.fits.fz")}
    report = audit_dataset_root(root, field=FIELD, pitch_arcsec=2.)
    assert report["schema"] == SCHEMA
    assert report["counts"]["total"] == 3
    assert report["counts"]["validated"] == 3
    assert len(report["multi_brick_groups"]) == 2
    south = next(g for g in report["multi_brick_groups"]
                 if g["region"] == "south")
    north = next(g for g in report["multi_brick_groups"]
                 if g["region"] == "north")
    assert len(south["per_brick"]) == 2
    assert len(north["per_brick"]) == 1
    assert south["unique_usable_samples"] > north["unique_usable_samples"]
    assert south["overlapping_geometric_samples"] > 0
    assert south["unique_usable_samples"] <= south["samples"]
    assert all(d["status"] == "FITS_WCS_VALIDATED_DIAGNOSTIC"
               for d in report["datasets"])
    assert all(d["products"]["image"]["sha256_local"]
               for d in report["datasets"])
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*.fits.fz")} == files_before


def test_validation_rejects_bad_wcs_corrupted_fits_and_zero_length(tmp_path):
    root = tmp_path / "data"
    _make_brick(root, "0401p012", shifted_wcs="nexp")
    _make_brick(root, "0400p012", corrupt="image")
    paths = _make_brick(root, "0402p012")
    paths["invvar"].write_bytes(b"")
    result = audit_dataset_root(root, field=FIELD)
    assert result["counts"]["validated"] == 0
    assert result["counts"]["incomplete_or_invalid"] == 3
    statuses = {d["brick"]: d for d in result["datasets"]}
    assert statuses["0401p012"]["status"] == "INVALID_FITS"
    assert "WCS" in statuses["0401p012"]["error"]
    assert statuses["0400p012"]["status"] == "INVALID_FITS"
    assert statuses["0402p012"]["status"] == "EMPTY_FILE"
    assert result["multi_brick_groups"] == []


def test_maskbits_saturated_samples_reduce_valid_without_losing_geometry(tmp_path):
    root = tmp_path / "data"
    _make_brick(root, "0401p012", satur=True)
    report = audit_dataset_root(root, field=FIELD, pitch_arcsec=1.)
    d = report["datasets"][0]
    assert d["fractions"]["instrumentally_usable"] < 1.
    grp = report["multi_brick_groups"][0]
    assert grp["unique_geometric_samples"] > grp["unique_usable_samples"]
    assert grp["overlapping_geometric_samples"] == 0


def test_orphan_maskbits_has_no_invented_band(tmp_path):
    root = tmp_path / "data"
    files = _make_brick(root, "0401p012")
    for kind in ("image", "invvar", "nexp"):
        files[kind].unlink()
    rows = discover_dataset_inventory(root)
    assert len(rows) == 1
    assert rows[0]["status"] == "ORPHAN_MASKBITS"
    assert rows[0]["band"] is None


def test_audit_receipts_immutable_outside_original_fits_tree(tmp_path):
    root = tmp_path / "UGC_2162"
    data = root / "data"
    _make_brick(data, "0401p012")
    report = audit_dataset_root(data, field=FIELD)
    one = save_dataset_audit(report, root, project_id="test-uuid")
    two = save_dataset_audit(report, root, project_id="test-uuid")
    assert one != two
    assert one.parent.parent == two.parent.parent == root / "dataset-audits"
    assert one.name == "audit.json"
    assert json.loads(one.read_text())["project_id"] == "test-uuid"
    assert not list(data.rglob("audit.json"))


def test_audit_limits_fail_closed_and_no_network(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _make_brick(root, "0401p012")
    with pytest.raises(ValueError, match="grid"):
        audit_dataset_root(root, field=SkyField(
            FIELD.ra_deg, FIELD.dec_deg, 40.), pitch_arcsec=1.)
    with pytest.raises(ValueError, match="audit limit"):
        audit_dataset_root(root, max_datasets=0)
    with pytest.raises(ValueError, match="Sample pitch"):
        audit_dataset_root(root, field=FIELD, pitch_arcsec=0)
