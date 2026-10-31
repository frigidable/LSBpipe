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
