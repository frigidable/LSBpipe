"""P3 pilot: a reproducible, conservative four-FITS Legacy field trial.

Network access is limited to the official release catalogs, optional HEAD
verification and explicitly requested downloads. An archive path by itself
is NEVER evidence that the FITS exists or covers the requested sky position.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json

from .legacy import SkyField, BrickHit, build_manifest, save_manifest
from .transfer import probe_product, download_manifest, _validated_product


SCIENCE_KINDS = ("image", "invvar", "nexp", "maskbits")


def science_pilot_manifest(field: SkyField, hits: list[BrickHit], *,
                           release: str, region: str, brick: str,
                           band: str) -> dict:
    """Select one unambiguous release+region+brick and exactly four products.

    North and south are independent reductions, not duplicate files. Do not
    select a region from brick-median exposure counts automatically.
    """
    matching = [hit for hit in hits
                if (hit.release, hit.region, hit.name) ==
                (release, region, brick)]
    if len(matching) != 1:
        raise ValueError(
            f"Requested {release}/{region}/{brick} has "
            f"{len(matching)} matching release records (expected exactly 1)")
    hit = matching[0]
    if band not in hit.median_exposures:
        raise ValueError(
            f"Band {band!r} is not listed for {release}/{region}/{brick}")
    manifest = build_manifest(
        field, [hit], kinds=SCIENCE_KINDS, bands=(band,))
    products = manifest["products"]
    expected = {(kind, None if kind == "maskbits" else band)
                for kind in SCIENCE_KINDS}
    actual = {(p["kind"], p["band"]) for p in products}
    if len(products) != 4 or actual != expected:
        raise ValueError("Science quartet incomplete or ambiguous")
    for product in products:
        _validated_product(product)
    manifest["pilot"] = {
        "schema": "lsbpipe.real-fits-pilot/0.1",
        "release": release, "region": region, "brick": brick,
        "band": band,
        "status": "PLANNED_NOT_DOWNLOADED",
        "median_brick_nexp": hit.median_exposures[band],
        "note": (
            "Brick median exposures are not per-pixel data. A science "
            "diagnostic requires downloading all four original FITS and "
            "checking celestial WCS, IVAR, NEXP and MASKBITS."
        ),
    }
    return manifest


def probe_science_quartet(manifest: dict) -> dict:
    """Probe each official URL via HEAD; do not equate unknown with missing."""
    if len(manifest.get("products", ())) != 4:
        raise ValueError("A single four-product pilot is required")
    result = json.loads(json.dumps(manifest))
    for product in result["products"]:
        product.update(probe_product(product))
    result["pilot"]["status"] = "URL_EVIDENCE_COLLECTED_NOT_DOWNLOADED"
    return result



def _matching_product_set(candidate: dict, current: dict) -> bool:
    """Prior evidence is only reusable for the EXACT original quartet."""
    if (candidate.get("schema") != current["schema"] or
            candidate.get("field") != current["field"] or
            candidate.get("pilot", {}).get("release") != current["pilot"]["release"] or
            candidate.get("pilot", {}).get("region") != current["pilot"]["region"] or
            candidate.get("pilot", {}).get("brick") != current["pilot"]["brick"] or
            candidate.get("pilot", {}).get("band") != current["pilot"]["band"]):
        return False
    def identity(entry):
        return (entry["relative_path"], entry["url"], entry["kind"],
                entry["band"], entry["release"], entry["region"], entry["brick"])
    try:
        old = [identity(p) for p in candidate["products"]]
        new = [identity(p) for p in current["products"]]
    except (KeyError, TypeError, ValueError):
        return False
    return len(old) == len(new) == 4 and len(set(old)) == 4 and set(old) == set(new)


def _prior_head_evidence(manifest: dict, root: Path) -> tuple[dict, str]:
    """Restore a previously *recorded* HEAD result, never claim a new probe.

    A dedicated pilot-probe.json is an audit snapshot. For pilots created
    before its introduction, a prior checked pilot-manifest.json can be
    used if it still exists. Invalid/mismatched prior evidence is ignored.
    """
    for filename in ("pilot-probe.json", "pilot-manifest.json"):
        try:
            previous = json.loads((root / filename).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(previous, dict) or not _matching_product_set(previous, manifest):
            continue
        by_path = {p["relative_path"]: p for p in previous["products"]}
        if not any(p.get("evidence") != "UNVERIFIED_URL"
                   for p in previous["products"]):
            continue
        result = json.loads(json.dumps(manifest))
        for product in result["products"]:
            prior = by_path[product["relative_path"]]
            for key in ("evidence", "http_status", "size_bytes", "error"):
                if key in prior:
                    product[key] = prior[key]
        timestamp = previous.get("pilot", {}).get("last_probed_at_utc")
        if timestamp:
            result["pilot"]["last_probed_at_utc"] = timestamp
        result["pilot"]["status"] = "PREVIOUS_HEAD_EVIDENCE_NOT_RECHECKED"
        return result, "PREVIOUS_RUN_NOT_RECHECKED"
    return manifest, "NOT_CHECKED"


def _prior_download_receipt(root: Path, field: SkyField,
                            selected: set[str]) -> Path | None:
    """Reference a matching local transfer receipt without trusting it as QA."""
    source = root / "data" / "download-report.json"
    try:
        receipt = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(receipt, dict):
        return None
    rows = receipt.get("results")
    if (receipt.get("schema") != "lsbpipe.legacy-download-report/0.1" or
            receipt.get("field") != asdict(field) or
            not isinstance(rows, list) or len(rows) != 4):
        return None
    paths = [row.get("relative_path") for row in rows
             if isinstance(row, dict)]
    if len(paths) != 4 or set(paths) != selected:
        return None
    return source

def run_science_pilot(
    field: SkyField, hits: list[BrickHit], *, release: str,
    region: str, brick: str, band: str, directory: Path,
    check_urls: bool = False, download: bool = False,
    inspect: bool = False, inspect_square: bool = False,
    target_radius_arcsec: float = 35.,
    sky_inner_arcsec: float = 65., sky_outer_arcsec: float = 110.,
) -> dict:
    """Run one explicit pilot with bounded files and preserved provenance.

    Dry-run writes a manifest only, not a claim of FITS availability.
    --download is an explicit user action. --inspect may also evaluate
    previously downloaded files, without re-fetching them.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    manifest = science_pilot_manifest(
        field, hits, release=release, region=region, brick=brick, band=band)
    if check_urls or download:
        manifest = probe_science_quartet(manifest)
        manifest["pilot"]["last_probed_at_utc"] = (
            datetime.now(timezone.utc).isoformat())
        head_source = "CURRENT_RUN"
        # Keep a separate evidence snapshot: --inspect and dry-run must
        # never erase successful HEAD evidence from an earlier invocation.
        save_manifest(manifest, root / "pilot-probe.json")
    else:
        manifest, head_source = _prior_head_evidence(manifest, root)
    manifest_path = save_manifest(manifest, root / "pilot-manifest.json")
    selected = {p["relative_path"] for p in manifest["products"]}
    statuses = dict(Counter(p["evidence"] for p in manifest["products"]))
    report = {
        "schema": "lsbpipe.real-fits-pilot-report/0.1",
        "field": asdict(field),
        "release": release, "region": region, "brick": brick, "band": band,
        "selected_products": sorted(selected),
        "manifest": str(manifest_path.resolve()),
        "http_evidence": statuses,
        "http_evidence_source": head_source,
        "head_checked_at_utc": manifest["pilot"].get("last_probed_at_utc"),
        "state": ("URLS_PROBED_NOT_DOWNLOADED"
                  if check_urls and not download else "PLANNED_NOT_DOWNLOADED"),
        "data_root": str((root / "data").resolve()),
        "limits": [
            "Original coadd files only; 4 files from one release+region+brick",
            "Brick summary median NEXP cannot establish pixel coverage",
            "A single brick does not establish completeness for the entire field",
            "No sky subtraction, mosaic, photometric acceptance or LSB detection",
        ],
    }
    # A prior download receipt records the transfer, not an authoritative
    # archive checksum. Linking it does not upgrade scientific quality.
    receipt_path = _prior_download_receipt(root, field, selected)
    if receipt_path is not None:
        report["prior_download_receipt"] = str(receipt_path.resolve())
        report["prior_download_receipt_source"] = "LOCAL_PREVIOUS_RUN"
    if download:
        if statuses.get("HTTP_MISSING", 0):
            report["state"] = "URL_MISSING_DOWNLOAD_ABORTED"
            report_path = root / "pilot-report.json"
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8")
            raise RuntimeError(
                "At least one required FITS URL returned HTTP 404/410. "
                "Pilot download aborted; examine pilot-report.json")
        receipt = download_manifest(
            manifest, root / "data", product_paths=selected,
            continue_on_error=True)
        report["download"] = receipt
        report["state"] = (
            "FOUR_FILES_FETCHED_OR_REUSED_NEEDS_FITS_QA"
            if receipt["complete"] else "INCOMPLETE_DOWNLOAD")
        if not receipt["complete"]:
            report_path = root / "pilot-report.json"
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8")
            raise RuntimeError(
                f"Pilot downloaded incompletely; see {report_path}")
    if inspect:
        from .coverage import inspect_local_brick, save_coverage
        try:
            qa = inspect_local_brick(
                root / "data", release=release, region=region, brick=brick,
                band=band, ra_deg=field.ra_deg, dec_deg=field.dec_deg,
                target_radius_arcsec=target_radius_arcsec,
                sky_inner_arcsec=sky_inner_arcsec,
                sky_outer_arcsec=sky_outer_arcsec)
            coverage_path = save_coverage(qa, root / "pilot-coverage.json")
        except Exception as exc:
            # Persist a failed attempt; never leave the previous success
            # report masquerading as the latest attempted inspection.
            report["state"] = "FITS_QA_FAILED"
            report["inspection_error"] = f"{type(exc).__name__}: {exc}"
            (root / "pilot-report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8")
            raise
        report["coverage"] = str(coverage_path.resolve())
        report["state"] = "FITS_QA_DIAGNOSTIC_NOT_ADOPTED"
    if inspect_square:
        from .square_coverage import inspect_local_square, save_square_coverage
        try:
            square = inspect_local_square(
                root / "data", release=release, region=region, brick=brick,
                band=band, field=field)
            square_path = save_square_coverage(
                square, root / "pilot-square-coverage.json")
        except Exception as exc:
            report["state"] = "SQUARE_FITS_QA_FAILED"
            report["square_inspection_error"] = (
                f"{type(exc).__name__}: {exc}")
            (root / "pilot-report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8")
            raise
        report["square_coverage"] = str(square_path.resolve())
        report["state"] = (
            "SQUARE_AND_CIRCLE_FITS_QA_DIAGNOSTIC_NOT_ADOPTED"
            if inspect else "SQUARE_FITS_QA_DIAGNOSTIC_NOT_ADOPTED")
    report_path = root / "pilot-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
