r"""Leo P FIRST PSF-0 pilot: offline native-FITS workflow (no downloads).

Examples (Windows PowerShell):
  .\.venv\Scripts\python.exe scripts/psf0_leo_p.py --project-root "C:\\...\\Leo_P"
  .\.venv\Scripts\python.exe scripts/psf0_leo_p.py --project-root "C:\\...\\Leo_P" --bands r,g,z --save-diagnostic

The default is a read-only pilot. The optional save creates immutable
diagnostic files in the named project's analyses/psf-pilots/ directory.
Never treats the PSF as validated or a galaxy's photometric measurement.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lsbpipe.photometry import load_native_legacy_quartet
from lsbpipe.photometry.psf_empirical import PSF0Config, build_empirical_coadd_psf
from lsbpipe.photometry.psf_export import export_psf0_diagnostic
from lsbpipe.surveys.dataset_integrity import _expected_paths
from lsbpipe.surveys.object_projects import read_object_project

LEO_P_WORKING_ICRS = (155.438012, 18.088025)
LEO_P_SCHEMA = "desidera.leo-p-psf0-pilot/1"


def _leo_p_identity(project):
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    if (project.display_name.strip().casefold().replace(" ", "") != "leop"
            or project.field is None):
        raise ValueError("PSF-0 first-control pilot is explicitly restricted to Leo P")
    saved_center = SkyCoord(project.field.ra_deg*u.deg, project.field.dec_deg*u.deg)
    working_center = SkyCoord(LEO_P_WORKING_ICRS[0]*u.deg,
                              LEO_P_WORKING_ICRS[1]*u.deg)
    separation = float(saved_center.separation(working_center).arcsec)
    if separation > 45.:
        raise ValueError(
            f"Saved project differs by {separation:.1f} arcsec from the "
            "Leo P working centre; verify object identity")
    return separation


def inspect_leo_p_inputs(project_root: str | Path, bands=("r", "g", "z")):
    """Read-only pilot preflight on saved Leo P project and selected brick.

    Does not switch datasets, download or create output folders. This only
    checks file paths; the subsequent native adapter is responsible for
    true FITS, WCS, unit and local SHA-256 verification.
    """
    project = read_object_project(project_root)
    offset = _leo_p_identity(project)
    selection = project.selected_dataset
    if selection is None or project.data_root is None:
        raise ValueError("Leo P project needs a selected native dataset and data root")
    release, region, brick, reference_band = selection
    if not isinstance(bands, (tuple, list)) or not bands or any(
            b not in ("g", "r", "i", "z") for b in bands):
        raise ValueError("Choose actual optical band names")
    if region == "north" and "i" in bands:
        raise ValueError("North lacks a native optical i band")
    if len(set(bands)) != len(bands):
        raise ValueError("Duplicate optical band")
    records = []
    for band in bands:
        paths = _expected_paths(project.data_root, (release, region, brick, band))
        # The selected band is the only one guaranteed to be associated
        # with the project. Other bands are a *candidate* to inspect;
        # no silent download / source adoption occurs.
        items = {name: {"path": str(path),
                        "exists": path.is_file() and not path.is_symlink()}
                 for name, path in paths.items()}
        records.append({"band": band, "products": items,
                        "state": "FILES_PRESENT_UNVERIFIED"
                        if all(x["exists"] for x in items.values())
                        else "MISSING_OR_UNSAFE_NATIVE_FILES",
                        "is_selected_band": band == reference_band})
    return {
        "schema": LEO_P_SCHEMA, "project_id": project.project_id,
        "object": project.display_name,
        "working_center_icrs_deg": list(LEO_P_WORKING_ICRS),
        "saved_center_icrs_deg": [project.field.ra_deg, project.field.dec_deg],
        "working_center_offset_arcsec": offset,
        "dataset": {"release": release, "region": region, "brick": brick,
                    "selected_band": reference_band},
        "bands": records, "status": "PILOT_PREFLIGHT_NOT_SCIENCE_ADOPTED",
    }


def run_leo_p_pilot(project_root: str | Path, *, bands=("r", "g", "z"),
                    config: PSF0Config | None = None,
                    protection_arcsec: float | None = None,
                    save_diagnostic: bool = False) -> dict:
    """Actual PSF-0 requires real original FITS; never manufactures mock data."""
    preflight = inspect_leo_p_inputs(project_root, bands)
    project = read_object_project(project_root)
    release, region, brick, _ = project.selected_dataset
    radius = (max(project.target_radius_arcsec, 90.)
              if protection_arcsec is None else float(protection_arcsec))
    if not math.isfinite(radius) or not 0 < radius <= 1800:
        raise ValueError("Invalid explicit Leo P protection radius")
    outcome = {"preflight": preflight, "target_protection_arcsec": radius,
               "band_results": [], "scientific_status": "PSF0_DIAGNOSTIC_NOT_ADOPTED"}
    cfg = config or PSF0Config()
    for entry in preflight["bands"]:
        band = entry["band"]
        if entry["state"] != "FILES_PRESENT_UNVERIFIED":
            outcome["band_results"].append({
                "band":band, "status":"SKIPPED_MISSING_NATIVE_QUARTET",
                "reason":"No network or automatic downloads permitted."})
            continue
        try:
            frame = load_native_legacy_quartet(
                project.data_root, release=release, region=region, brick=brick,
                band=band, hash_inputs=True, require_explicit_bunit=True)
            result = build_empirical_coadd_psf(
                frame, target_icrs_deg=LEO_P_WORKING_ICRS,
                target_protection_arcsec=radius, config=cfg)
            report = result.report()
            if save_diagnostic:
                location = export_psf0_diagnostic(
                    result, project.root / "analyses" / "psf-pilots",
                    explicitly_requested=True)
                report["diagnostic_saved_to"] = str(location)
            outcome["band_results"].append({
                "band":band, "status":result.status, "report":report})
        except (ValueError, RuntimeError, OSError) as exc:
            # Explicit per-band failure rather than a fabricated/partial PSF.
            outcome["band_results"].append({
                "band":band, "status":"PSF0_FAILED_NOT_ADOPTED",
                "reason":str(exc)})
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Leo P PSF-0 first pilot: read-only by default, no downloads")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--bands", nargs="+", choices=("g", "r", "i", "z"),
                        default=["r", "g", "z"])
    parser.add_argument("--fwhm-pix", type=float, default=4.8,
                        help="Explicit initial guess, not a measured seeing")
    parser.add_argument("--target-radius-arcsec", type=float, default=None,
                        help="Override conservative protected Leo P region")
    parser.add_argument("--save-diagnostic", action="store_true",
                        help="Explicitly write immutable experimental PSF FITS + QA")
    args = parser.parse_args()
    cfg = PSF0Config(guessed_fwhm_pix=args.fwhm_pix)
    report = run_leo_p_pilot(
        args.project_root, bands=args.bands, config=cfg,
        protection_arcsec=args.target_radius_arcsec,
        save_diagnostic=args.save_diagnostic)
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if any(row.get("status") == "EXPERIMENTAL_PSF_NOT_ADOPTED"
                    for row in report["band_results"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
