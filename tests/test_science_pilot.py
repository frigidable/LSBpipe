"""Real-FITS pilot selection and orchestration: deterministic offline tests."""
import json
import pytest

from lsbpipe.surveys.legacy import SkyField, BrickHit
from lsbpipe.surveys.science_pilot import (
    SCIENCE_KINDS, science_pilot_manifest, probe_science_quartet,
    run_science_pilot,
)


FIELD = SkyField(40.096208, 1.229250, 5)


def hits():
    return [
        BrickHit("dr9", "north", "0401p012", 40.0, 40.2, 1.1, 1.35,
                 {"g": 3, "r": 4, "z": 4}),
        BrickHit("dr9", "south", "0401p012", 40.0, 40.2, 1.1, 1.35,
                 {"g": 9, "r": 9, "z": 9}),
    ]


def plan(region="south", band="r"):
    return science_pilot_manifest(
        FIELD, hits(), release="dr9", region=region,
        brick="0401p012", band=band)


def test_two_dr9_regions_remain_independent_products():
    north, south = plan("north"), plan("south")
    assert len(north["products"]) == len(south["products"]) == 4
    assert {p["kind"] for p in south["products"]} == set(SCIENCE_KINDS)
    assert {p["band"] for p in south["products"]} == {"r", None}
    assert all("/dr9/south/" in p["url"] for p in south["products"])
    assert all("/dr9/north/" in p["url"] for p in north["products"])
    assert ({p["relative_path"] for p in north["products"]} &
            {p["relative_path"] for p in south["products"]}) == set()
    assert north["pilot"]["median_brick_nexp"] == 4
    assert south["pilot"]["median_brick_nexp"] == 9
    assert south["pilot"]["status"] == "PLANNED_NOT_DOWNLOADED"
    assert all(p["evidence"] == "UNVERIFIED_URL" for p in south["products"])


@pytest.mark.parametrize("region,brick,band", [
    ("north", "9999p012", "r"), ("south", "0401p012", "i"),
    ("nonsense", "0401p012", "r"),
])
def test_incorrect_selection_cannot_make_fake_available_quartet(
        region, brick, band):
    with pytest.raises(ValueError):
        science_pilot_manifest(FIELD, hits(), release="dr9",
                               region=region, brick=brick, band=band)


def test_duplicate_release_records_rejected():
    with pytest.raises(ValueError, match="2 matching"):
        science_pilot_manifest(
            FIELD, hits() + hits()[:1], release="dr9",
            region="north", brick="0401p012", band="r")


def test_probe_updates_all_four_without_changing_original(monkeypatch):
    original = plan()
    seen = []
    def fake_probe(entry):
        seen.append(entry["kind"])
        return {"evidence": "HTTP_OK", "http_status": 200,
                "size_bytes": 100}
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product", fake_probe)
    result = probe_science_quartet(original)
    assert len(seen) == 4
    assert set(seen) == set(SCIENCE_KINDS)
    assert all(e["evidence"] == "HTTP_OK" for e in result["products"])
    assert all(e["evidence"] == "UNVERIFIED_URL" for e in original["products"])
    assert result["pilot"]["status"] == "URL_EVIDENCE_COLLECTED_NOT_DOWNLOADED"


def test_dry_run_writes_exact_manifest_without_network(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("A dry run must never reach the archive")
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product", no_network)
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest", no_network)
    result = run_science_pilot(
        FIELD, hits(), release="dr9", region="south",
        brick="0401p012", band="r", directory=tmp_path)
    assert result["state"] == "PLANNED_NOT_DOWNLOADED"
    assert len(result["selected_products"]) == 4
    assert not (tmp_path / "data").exists()
    saved = json.loads((tmp_path/"pilot-manifest.json").read_text("utf-8"))
    assert saved["schema"] == "lsbpipe.legacy-download/0.1"
    assert saved["field"]["width_arcmin"] == 5
    assert json.loads((tmp_path/"pilot-report.json").read_text("utf-8"))[
        "state"] == "PLANNED_NOT_DOWNLOADED"


def test_explicit_download_passes_only_four_paths_and_saves_receipt(
        tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK", "size_bytes": 456})
    def get(manifest, directory, *, product_paths, continue_on_error):
        seen.append((manifest, directory, product_paths, continue_on_error))
        return {"complete": True, "results": [
            {"state": "DOWNLOADED", "relative_path": p}
            for p in sorted(product_paths)]}
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest", get)
    report = run_science_pilot(
        FIELD, hits(), release="dr9", region="south",
        brick="0401p012", band="r", directory=tmp_path,
        download=True)
    assert report["state"] == "FOUR_FILES_FETCHED_OR_REUSED_NEEDS_FITS_QA"
    assert len(seen) == 1 and len(seen[0][2]) == 4
    assert seen[0][1] == tmp_path/"data"
    assert seen[0][3]
    assert len(report["download"]["results"]) == 4


def test_http_missing_aborts_download_before_get(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_MISSING", "http_status": 404})
    def no_get(*args, **kwargs):
        raise AssertionError("GET on a known 404 product is forbidden")
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest", no_get)
    with pytest.raises(RuntimeError, match="404/410"):
        run_science_pilot(
            FIELD, hits(), release="dr9", region="north",
            brick="0401p012", band="r", directory=tmp_path, download=True)
    assert (tmp_path/"pilot-manifest.json").is_file()
    assert not (tmp_path/"data").exists()
    report = json.loads((tmp_path/"pilot-report.json").read_text("utf-8"))
    assert report["state"] == "URL_MISSING_DOWNLOAD_ABORTED"


def test_incomplete_download_records_noncomplete_pilot_report(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK"})
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest",
        lambda *args, **kwargs: {"complete": False,
                                 "results": [{"state": "ERROR"}]})
    with pytest.raises(RuntimeError, match="incompletely"):
        run_science_pilot(
            FIELD, hits(), release="dr9", region="south",
            brick="0401p012", band="r", directory=tmp_path, download=True)
    report = json.loads((tmp_path/"pilot-report.json").read_text("utf-8"))
    assert report["state"] == "INCOMPLETE_DOWNLOAD"


def test_head_only_probe_distinguished_from_actual_download(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK", "size_bytes": 1024})
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest",
        lambda *a, **k: pytest.fail("HEAD-only run triggered science GET"))
    out = run_science_pilot(
        FIELD, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path, check_urls=True)
    assert out["state"] == "URLS_PROBED_NOT_DOWNLOADED"
    assert out["http_evidence"] == {"HTTP_OK": 4}
    assert not (tmp_path/"data").exists()



def _mock_successful_fits_inspection(monkeypatch):
    from lsbpipe.surveys import coverage
    monkeypatch.setattr(
        coverage, "inspect_local_brick",
        lambda *args, **kwargs: {"status": "DIAGNOSTIC_NOT_ADOPTED"})
    def fake_save(result, path):
        path.write_text(json.dumps(result), encoding="utf-8")
        return path
    monkeypatch.setattr(coverage, "save_coverage", fake_save)


def test_inspect_only_preserves_previous_head_without_new_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK", "http_status": 200,
                       "size_bytes": 12345})
    result = run_science_pilot(
        FIELD, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path, check_urls=True)
    assert result["http_evidence_source"] == "CURRENT_RUN"
    snapshot = json.loads((tmp_path/"pilot-probe.json").read_text("utf-8"))
    assert all(p["evidence"] == "HTTP_OK" for p in snapshot["products"])
    assert result["head_checked_at_utc"]

    def no_network(*args, **kwargs):
        pytest.fail("A follow-up --inspect must not repeat HEAD or GET")
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product", no_network)
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest", no_network)
    _mock_successful_fits_inspection(monkeypatch)
    qa = run_science_pilot(
        FIELD, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path, inspect=True)
    assert qa["state"] == "FITS_QA_DIAGNOSTIC_NOT_ADOPTED"
    assert qa["http_evidence"] == {"HTTP_OK": 4}
    assert qa["http_evidence_source"] == "PREVIOUS_RUN_NOT_RECHECKED"
    assert qa["head_checked_at_utc"] == result["head_checked_at_utc"]
    assert (tmp_path/"pilot-coverage.json").is_file()
    persistent = json.loads((tmp_path/"pilot-manifest.json").read_text("utf-8"))
    assert all(e["evidence"] == "HTTP_OK" for e in persistent["products"])
    # Dedicated audit snapshot survives both inspect and later dry-run.
    run_science_pilot(FIELD, hits(), release="dr9", region="south",
                      brick="0401p012", band="r", directory=tmp_path)
    assert json.loads((tmp_path/"pilot-probe.json").read_text("utf-8")) == snapshot


def test_previous_http_evidence_cannot_leak_into_different_target(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK", "size_bytes": 100})
    run_science_pilot(FIELD, hits(), release="dr9", region="south",
                      brick="0401p012", band="r", directory=tmp_path,
                      check_urls=True)
    changed = SkyField(FIELD.ra_deg + 0.01, FIELD.dec_deg, FIELD.width_arcmin)
    result = run_science_pilot(
        changed, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path)
    assert result["http_evidence_source"] == "NOT_CHECKED"
    assert result["http_evidence"] == {"UNVERIFIED_URL": 4}
    # Snapshot with original coordinate provenance must remain immutable.
    assert json.loads((tmp_path/"pilot-probe.json").read_text("utf-8"))[
        "field"]["ra_deg"] == FIELD.ra_deg


def test_matching_local_download_receipt_linked_without_reclaiming_transfer(
        tmp_path):
    from dataclasses import asdict
    data = tmp_path/"data"
    data.mkdir()
    entries = plan()["products"]
    receipt = {
        "schema": "lsbpipe.legacy-download-report/0.1",
        "field": asdict(FIELD),
        "complete": True,
        "results": [
            {"relative_path": e["relative_path"],
             "state": "DOWNLOADED", "sha256": "abc"}
            for e in entries],
    }
    (data/"download-report.json").write_text(
        json.dumps(receipt), encoding="utf-8")
    result = run_science_pilot(
        FIELD, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path)
    assert result["prior_download_receipt"] == str(
        (data/"download-report.json").resolve())
    assert result["prior_download_receipt_source"] == "LOCAL_PREVIOUS_RUN"
    changed = SkyField(FIELD.ra_deg+0.01, FIELD.dec_deg, FIELD.width_arcmin)
    different = run_science_pilot(
        changed, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path)
    assert "prior_download_receipt" not in different


def test_failing_fits_qa_persists_failure_without_erasing_probe(
        tmp_path, monkeypatch):
    from lsbpipe.surveys import coverage
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda entry: {"evidence": "HTTP_OK"})
    run_science_pilot(
        FIELD, hits(), release="dr9", region="south", brick="0401p012",
        band="r", directory=tmp_path, check_urls=True)
    def fail(*args, **kwargs):
        raise ValueError("original FITS WCS mismatch")
    monkeypatch.setattr(coverage, "inspect_local_brick", fail)
    with pytest.raises(ValueError, match="WCS mismatch"):
        run_science_pilot(
            FIELD, hits(), release="dr9", region="south", brick="0401p012",
            band="r", directory=tmp_path, inspect=True)
    report = json.loads((tmp_path/"pilot-report.json").read_text("utf-8"))
    assert report["state"] == "FITS_QA_FAILED"
    assert "WCS mismatch" in report["inspection_error"]
    assert report["http_evidence"] == {"HTTP_OK": 4}
    assert report["http_evidence_source"] == "PREVIOUS_RUN_NOT_RECHECKED"
    assert (tmp_path/"pilot-probe.json").exists()



def test_square_only_inspection_uses_local_files_without_head_or_get(
        tmp_path, monkeypatch):
    from lsbpipe.surveys import square_coverage
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.probe_product",
        lambda *args, **kwargs: pytest.fail("Inspect square must not call HEAD"))
    monkeypatch.setattr(
        "lsbpipe.surveys.science_pilot.download_manifest",
        lambda *args, **kwargs: pytest.fail("Inspect square must not download"))
    calls = []
    def inspect(directory, *, release, region, brick, band, field):
        calls.append((directory, release, region, brick, band, field))
        return {"status": "DIAGNOSTIC_NOT_ADOPTED",
                "polygon_within_single_brick": True}
    def save(report, path):
        path.write_text(json.dumps(report), encoding="utf-8")
        return path
    monkeypatch.setattr(square_coverage, "inspect_local_square", inspect)
    monkeypatch.setattr(square_coverage, "save_square_coverage", save)
    result = run_science_pilot(
        FIELD, hits(), release="dr9", region="south",
        brick="0401p012", band="r", directory=tmp_path,
        inspect_square=True)
    assert result["state"] == "SQUARE_FITS_QA_DIAGNOSTIC_NOT_ADOPTED"
    assert result["http_evidence_source"] == "NOT_CHECKED"
    assert len(calls) == 1
    assert calls[0][0] == tmp_path / "data"
    assert calls[0][-1] == FIELD
    assert json.loads((tmp_path / "pilot-square-coverage.json").read_text(
        "utf-8"))["polygon_within_single_brick"] is True


def test_square_failure_produces_durable_failed_report(tmp_path, monkeypatch):
    from lsbpipe.surveys import square_coverage
    def refuse(*args, **kwargs):
        raise ValueError("Malformed original square WCS")
    monkeypatch.setattr(square_coverage, "inspect_local_square", refuse)
    with pytest.raises(ValueError, match="WCS"):
        run_science_pilot(
            FIELD, hits(), release="dr9", region="south",
            brick="0401p012", band="r", directory=tmp_path,
            inspect_square=True)
    recorded = json.loads((tmp_path/"pilot-report.json").read_text("utf-8"))
    assert recorded["state"] == "SQUARE_FITS_QA_FAILED"
    assert "WCS" in recorded["square_inspection_error"]
    assert not (tmp_path/"pilot-square-coverage.json").exists()
