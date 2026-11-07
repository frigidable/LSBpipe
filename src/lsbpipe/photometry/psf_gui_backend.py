"""GUI orchestration for the Leo P-first PSF-0 experiment.

No Qt imports; work is executed by existing SurveyExplorer TaskWorker.
Uses the saved object-project manifest and the original legacy FITS tree.
Never creates a second project, moves a source selection, downloads, or
changes main project state. The selected analysis band is independent of
the saved Galaxy Workspace *default band*.
"""
from __future__ import annotations

from hashlib import sha256
from dataclasses import replace, asdict
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Callable

from lsbpipe.photometry.native_fits import load_native_legacy_quartet
from lsbpipe.photometry.psf_empirical import (
    PSF0Config, PSF0Result, PSF0QualityError, build_empirical_coadd_psf,
)
from lsbpipe.photometry.psf_export import export_psf0_diagnostic
from lsbpipe.photometry.psf_tractor_gaia import (
    tractor_catalog_location, read_native_tractor_gaia,
    download_native_tractor_gaia,
)
from lsbpipe.surveys.dataset_integrity import _expected_paths
from lsbpipe.surveys.object_projects import read_object_project

LEO_P_CENTER = (155.438012, 18.088025)
BANDS = ("g", "r", "i", "z")
KINDS = ("image", "invvar", "nexp", "maskbits")

def _same_observation_source(current, recorded) -> bool:
    """Saved default band is navigation state, not PSF source identity.

    A PSF is bound to its own band through dataset_id/review_band, while
    the observation source for all four panels is release/region/brick.
    Invalid or partial identities always fail closed.
    """
    if not isinstance(current, (tuple, list)) or not isinstance(recorded, (tuple, list)):
        return False
    if len(current) != 4 or len(recorded) != 4:
        return False
    return tuple(current[:3]) == tuple(recorded[:3])


# Six reproducible diagnostic modes. The extended modes jointly change
# cutout, sky-annulus and isolation footprints. Their
# stellar sample can therefore differ: do not claim a controlled
# single-variable causal inference or select a "best PSF" from these QA
# summaries. Every experiment remains non-adopted.
PSF0_DIAGNOSTIC_EXPERIMENTS = {
    "baseline": {
        "label": "Baseline · fixed 31 px / sky 10–15 px (wing-sensitive)",
        "kwargs": {},
    },
    "outer_sky": {
        "label": "Extended · FWHM-scaled sky 4.5–5.75× (pilot)",
        "kwargs": {"training_outlier_passes": 0},
    },
    "outer_sky_robust": {
        "label": "Extended sky + guarded iterative star QA (2 passes)",
        "kwargs": {"training_outlier_passes": 2},
    },
    "outer_sky_convergence": {
        "label": "Extended sky · 16 solver iterations (convergence control)",
        "kwargs": {"training_outlier_passes": 0, "max_iterations": 16},
    },
    "outer_sky_no_wing_smoothing": {
        "label": "Extended sky · 16 iterations · wing smoothing OFF (controlled A/B)",
        "kwargs": {
            "training_outlier_passes": 0,
            "max_iterations": 16,
            "wing_smoothing": False,
        },
    },
    "outer_sky_isolation_footprint": {
        "label": "Extended sky · 16 iterations · nearest DAO separation ≥ stamp size (g pilot)",
        "kwargs": {
            "training_outlier_passes": 0,
            "max_iterations": 16,
        },
    },
}


def psf0_experiment_recipe(
    guessed_fwhm_pix: float, experiment: str,
) -> PSF0Config:
    """Return a traceable experimental sky geometry, not a certified PSF.

    A fixed annulus at 19–24 pix was inside 3×–4× FWHM for the REAL Leo P
    DR11 g PSFSIZE prior of 6.57 pix. Extended modes therefore express the
    *initial* sky-sampling radius in FWHM units and fail closed if the
    necessary cutout cannot fit safely under the 91-pixel PSF0 limit.

    Even 4.5× FWHM does NOT guarantee wing-free sky. The science report
    continues to flag the sky/PSF degeneracy and correlated-coadd noise.
    The baseline is deliberately fixed for a diagnostic reference only.
    """
    if experiment not in PSF0_DIAGNOSTIC_EXPERIMENTS:
        raise ValueError("Unknown PSF-0 diagnostic experiment")
    fwhm = float(guessed_fwhm_pix)
    if not math.isfinite(fwhm) or not 2.5 <= fwhm <= 20:
        raise ValueError("PSF FWHM prior must be finite and in 2.5..20 pix")
    kw = dict(PSF0_DIAGNOSTIC_EXPERIMENTS[experiment]["kwargs"])
    if experiment != "baseline":
        # Integer annulus boundaries are deterministic, in native pixels;
        # enforce 2 pixels beyond outer sky before the square stamp edge.
        inner = float(math.ceil(4.5*fwhm))
        outer = float(math.ceil(5.75*fwhm))
        half = int(outer)+2
        size = max(51, 2*half+1)
        if size > 91:
            raise ValueError(
                f"FWHM-scaled sky annulus needs {size} px for assumed "
                f"FWHM {fwhm:.2f} px, above the 91 px PSF0 limit. "
                "Choose baseline as a wing-sensitive diagnostic, not as "
                "a substitute for validated extended-wing background; "
                "do not silently shrink the annulus.")
        kw.update(
            cutout_size_pix=size,
            sky_annulus_inner_pix=inner,
            sky_annulus_outer_pix=outer,
            min_neighbor_separation_pix=float(size+4),
        )
        if experiment == "outer_sky_isolation_footprint":
            # Registered, deliberately separate one-factor A/B:
            # centre-to-centre DAO isolation of one FULL stamp width
            # rather than full width + 4 px. In the real Leo P g
            # 81px stamp this recovers the possibility of evaluating
            # Gaia stars at 81..85 px from a DAO detection. Both local
            # sky annuli still have disjoint 38px radii, but faint
            # wings / unmodelled neighbours MAY contaminate either.
            # Never claim those candidate stamps are clean without
            # independent per-star screening, sky QA and heldout tests.
            kw["min_neighbor_separation_pix"] = float(size)
    return PSF0Config(guessed_fwhm_pix=fwhm, **kw)


def _leo_p_project(project_root):
    """Use existing UUID and saved source identity, never a guessed folder."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    project = read_object_project(project_root)
    if project.display_name.strip().casefold().replace(" ", "") != "leop":
        raise ValueError("PSF-0 real-object pilot currently supports Leo P only")
    if project.field is None or project.data_root is None or project.selected_dataset is None:
        raise ValueError("Leo P needs its saved coordinates, selected dataset and data root")
    center = SkyCoord(project.field.ra_deg*u.deg, project.field.dec_deg*u.deg)
    expected = SkyCoord(LEO_P_CENTER[0]*u.deg, LEO_P_CENTER[1]*u.deg)
    if center.separation(expected).arcsec > 45:
        raise ValueError("Leo P project center differs by over 45 arcsec; review galaxy identity")
    return project


def inspect_psf0_bands(project_root: str | Path) -> dict:
    """Metadata/path-only availability for the current Galaxy Workspace."""
    project = _leo_p_project(project_root)
    release, region, brick, selected = project.selected_dataset
    entries = []
    for band in BANDS:
        if region == "north" and band == "i":
            continue
        locations = _expected_paths(project.data_root, (release, region, brick, band))
        present = {kind: path.is_file() and not path.is_symlink()
                   for kind, path in locations.items()}
        entries.append({
            "band": band,
            "selected": band == selected,
            "present_count": sum(present.values()),
            "paths_present_unverified": all(present.values()),
            "files": present,
        })
    catalog_path = None
    catalog_present = False
    if release == "dr11" and region == "south":
        candidate_path, _ = tractor_catalog_location(
            project.data_root, release=release, region=region, brick=brick)
        catalog_path = str(candidate_path)
        catalog_present = (
            candidate_path.is_file() and not candidate_path.is_symlink())
    return {
        "project_id": project.project_id,
        "project_root": str(project.root),
        "dataset": (release, region, brick, selected),
        "bands": entries,
        "gaia_tractor_path": catalog_path,
        "gaia_tractor_present_unverified": catalog_present,
        "target_protection_default_arcsec": max(project.target_radius_arcsec, 90.),
        "status": "PATHS_ONLY_NOT_SCIENCE_VALIDATED",
    }


def _progress(progress: Callable | None, text: str):
    if progress is not None:
        progress((0, 1, text))
        checkpoint = getattr(progress, "checkpoint", None)
        if checkpoint is not None:
            checkpoint()


def _saved_manual_exclusions(project, source_hashes, *,
                             release, region, brick, band, config, radius,
                             catalog_sha256=None):
    """Read only matching source+recipe reviews; never silently apply one."""
    directory = project.root / "analyses" / "psf-pilots" / "star-reviews"
    if not directory.is_dir() or directory.is_symlink():
        return None
    required_recipe = asdict(config)
    required_recipe.pop("excluded_training_star_ids", None)
    candidates = []
    for path in list(directory.glob("review-*.json"))[:1000]:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf8"))
            recipe = data["recipe"]
            original_recipe = {k:v for k,v in recipe.items()
                               if k!="excluded_training_star_ids"}
            # Before this feature, only DAO candidates existed. Treat
            # those historical receipts as DAO-only; never as Gaia-strict.
            original_recipe.setdefault("star_catalog_policy", "dao_diagnostic")
            ids = data["excluded_training_star_ids"]
            if (data.get("schema") !=
                    "desidera.psf0-manual-training-review/1"
                    or data.get("project_id") != project.project_id
                    or data.get("dataset") != [release, region, brick, band]
                    or data.get("source_sha256_local") != source_hashes
                    or data.get("catalog_sha256_local") != catalog_sha256
                    or data.get("target_protection_arcsec") != radius
                    or original_recipe != required_recipe
                    or not isinstance(ids, list) or
                    any(not isinstance(k, str) for k in ids)):
                continue
            candidates.append((path.stat().st_mtime_ns, path, ids))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if not candidates:
        return None
    _, path, ids = max(candidates, key=lambda x: (x[0],str(x[1])))
    return {"excluded_training_star_ids": tuple(ids),
            "receipt_path": str(path), "auto_applied": False}


def run_gui_psf0(
    project_root: str | Path, *,
    band: str, config: PSF0Config,
    protection_arcsec: float, progress: Callable | None = None,
) -> dict:
    """Read, validate and build in background, return result without saving."""
    snapshot = inspect_psf0_bands(project_root)
    chosen = next((x for x in snapshot["bands"] if x["band"] == band), None)
    if chosen is None or not chosen["paths_present_unverified"]:
        raise ValueError(f"Selected {band} native FITS quartet is incomplete or unsupported")
    if not isinstance(config, PSF0Config):
        raise TypeError("PSF-0 configuration is required")
    if not math.isfinite(protection_arcsec) or not 0 < protection_arcsec <= 1800:
        raise ValueError("Invalid galaxy-protection radius")
    release, region, brick, _ = snapshot["dataset"]
    project = _leo_p_project(project_root)
    _progress(progress, f"Checking {release.upper()} {band}: original FITS and source SHA-256")
    frame = load_native_legacy_quartet(
        project.data_root, release=release, region=region, brick=brick,
        band=band, hash_inputs=True, require_explicit_bunit=True)
    catalogue = None
    if config.star_catalog_policy in ("gaia_strict", "tractor_psf_validated"):
        _progress(progress, "Validating native DR11 Tractor, Gaia and PSF identities")
        catalogue = read_native_tractor_gaia(
            project.data_root, release=release, region=region,
            brick=brick, frame=frame)
    previous_review = _saved_manual_exclusions(
        project, dict(frame.source_evidence.get("local_hashes", {})),
        release=release, region=region, brick=brick, band=band,
        config=config, radius=float(protection_arcsec),
        catalog_sha256=(catalogue.source_sha256 if catalogue else None))
    _progress(progress, f"Building provisional {band} PSF · star selection, sky, holdout")
    try:
        result = build_empirical_coadd_psf(
            frame, target_icrs_deg=LEO_P_CENTER,
            target_protection_arcsec=float(protection_arcsec), config=config,
            catalog_matcher=catalogue)
    except PSF0QualityError as exc:
        # This is a *normal scientific rejection* with useful candidate-star
        # evidence, not an exception that should lose context in a GUI popup.
        return {
            "status": "PSF0_QUALITY_REJECTED_NOT_ADOPTED",
            "review_band": band,
            "effective_recipe": asdict(config),
            "recipe_sha256": config.digest,
            "reason": str(exc),
            "diagnostics": exc.diagnostics,
            "star_review": exc.review_snapshot,
            "saved_manual_review": previous_review,
            "target_protection_arcsec": float(protection_arcsec),
            "source_hashes": dict(frame.source_evidence.get("local_hashes", {})),
            "project_id": snapshot["project_id"],
            "project_root": snapshot["project_root"],
            "dataset": (release, region, brick, band),
        }
    _progress(progress, f"Completed provisional {band} PSF · not science-adopted")
    return {
        "status": "EXPERIMENTAL_PSF_NOT_ADOPTED",
        "review_band": band,
        "effective_recipe": asdict(config),
        "recipe_sha256": config.digest,
        "result": result,
        "star_review": result.review_snapshot,
        "saved_manual_review": previous_review,
        "target_protection_arcsec": float(protection_arcsec),
        "source_hashes": dict(result.source_hashes),
        "project_id": snapshot["project_id"],
        "project_root": snapshot["project_root"],
        "dataset": (release, region, brick, band),
    }


def acquire_gui_psf0_tractor(project_root, *, progress=None):
    """Explicit user-requested native DR11 Tractor acquisition in Galaxy Workspace."""
    project = _leo_p_project(project_root)
    release, region, brick, _ = project.selected_dataset
    _progress(progress, "Getting original release- and brick-matched DR11 Tractor catalog")
    result = download_native_tractor_gaia(
        project.data_root, release=release, region=region,
        brick=brick, progress=progress)
    # Verify the same-brick source table immediately for an available band.
    snapshot = inspect_psf0_bands(project_root)
    bands = [row["band"] for row in snapshot["bands"]
             if row["paths_present_unverified"]]
    if not bands:
        raise ValueError("Tractor acquired but no complete optical FITS quartet")
    frame = load_native_legacy_quartet(
        project.data_root, release=release, region=region,
        brick=brick, band=bands[0], hash_inputs=False,
        require_explicit_bunit=True)
    matcher = read_native_tractor_gaia(
        project.data_root, release=release, region=region,
        brick=brick, frame=frame)
    return {
        **result,
        "project_id":project.project_id,
        "project_root":str(project.root),
        "n_catalog_sources":len(matcher.rows),
        "sha256_local":matcher.source_sha256,
        "verified_against_native_fits":True,
        "not_science_adopted":True,
    }


def suggest_gui_psf0_fwhm(project_root, *, band, progress=None):
    """Read optional original Legacy PSFSIZE on the current saved project."""
    from lsbpipe.photometry.psf_survey_defaults import (
        suggest_detection_fwhm_from_psfsize,
    )
    selection = inspect_psf0_bands(project_root)
    eligible = next((entry for entry in selection["bands"]
                     if entry["band"] == band), None)
    if eligible is None or not eligible["paths_present_unverified"]:
        raise ValueError("Native optical quartet is incomplete for PSFSIZE check")
    release, region, brick, _ = selection["dataset"]
    project = _leo_p_project(project_root)
    _progress(progress, "Checking image WCS and optional local Legacy PSFSIZE map")
    frame = load_native_legacy_quartet(
        project.data_root, release=release, region=region,
        brick=brick, band=band, hash_inputs=False,
        require_explicit_bunit=True)
    result = suggest_detection_fwhm_from_psfsize(
        frame, target_icrs_deg=LEO_P_CENTER)
    return {
        "project_id": project.project_id,
        "project_root": str(project.root),
        "band": band,
        "seeing": result,
        "protection": {
            "origin": "SAVED_PROJECT_RADIUS_WITH_EXPLICIT_90_ARCSEC_MINIMUM",
            "project_target_radius_arcsec": project.target_radius_arcsec,
            "current_default_arcsec": max(project.target_radius_arcsec, 90.),
            "science_adopted": False,
            "automatic_from_psf_fwhm": False,
        },
    }


def commit_psf0_manual_review(preview, *, explicitly_confirmed=False):
    """Write an immutable, project-bound exclusion receipt by deliberate action.

    No source FITS touched, no overwrite of existing distinct review data.
    Review decisions refer only to eligible training star IDs and cannot
    change the holdout set or promote a scientifically rejected PSF.
    """
    if explicitly_confirmed is not True:
        raise ValueError("Manual review must be explicitly applied in GUI")
    if not isinstance(preview, dict) or preview.get("star_review") is None:
        raise ValueError("Manual review requires a real completed star inventory")
    project = _leo_p_project(preview["project_root"])
    if (project.project_id != preview["project_id"] or
            not _same_observation_source(project.selected_dataset, preview.get("dataset"))):
        raise ValueError("Saved project identity changed during star review")
    source_hashes = preview.get("source_hashes", {})
    if set(source_hashes) != set(KINDS):
        raise ValueError("Missing validated source fingerprints for manual review")
    star_review = preview["star_review"]
    exclusions = tuple(star_review["excluded_training_star_ids"])
    if not exclusions:
        # An explicit restore-all still needs a derivative receipt; keep
        # the empty selection as an immutable scientific review decision.
        pass
    inventory = {entry["star_id"] for entry in star_review["stars"]}
    if set(exclusions) - inventory:
        raise ValueError("Manual exclusion contains a non-training candidate")
    release, region, brick, _ = project.selected_dataset
    band = preview.get("band")
    if band is None:
        if "result" in preview:
            band = preview["result"].dataset_id.split("/")[-1]
        else:
            band = preview.get("review_band")
    if band not in BANDS:
        raise ValueError("Manual review has no confirmed band")
    paths = _expected_paths(project.data_root, (release, region, brick, band))
    for kind, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError("Original source changed before saving star review")
        if _hash_original_file(path) != source_hashes[kind]:
            raise ValueError("Original FITS changed during manual star review; rerun")
    expected_catalog = star_review.get("catalog_sha256")
    if expected_catalog is not None:
        catalog_path, _ = tractor_catalog_location(
            project.data_root, release=release, region=region, brick=brick)
        if (not catalog_path.is_file() or catalog_path.is_symlink() or
                _hash_original_file(catalog_path) != expected_catalog):
            raise ValueError(
                "Original DR11 Tractor/Gaia catalogue changed since PSF "
                "review; do not persist stale star exclusions")
    receipt = {
        "schema": "desidera.psf0-manual-training-review/1",
        "status": "MANUAL_SELECTION_NOT_PSF_ADOPTION",
        "science_adopted": False,
        "project_id": project.project_id,
        "dataset": [release, region, brick, band],
        "source_sha256_local": dict(source_hashes),
        "catalog_sha256_local": star_review.get("catalog_sha256"),
        "target_protection_arcsec": float(preview["target_protection_arcsec"]),
        "recipe": (preview["result"].report()["recipe"] if "result" in preview
                   else preview["diagnostics"]["psf_recipe"]),
        "excluded_training_star_ids": list(exclusions),
        "training_candidates": [item["star_id"] for item in star_review["stars"]],
        "holdout_protected": True,
    }
    payload = json.dumps(receipt, sort_keys=True, indent=2,
                         allow_nan=False).encode("utf8")
    digest = sha256(payload).hexdigest()
    directory = project.root / "analyses" / "psf-pilots" / "star-reviews"
    for p in (directory, *directory.parents):
        if p.is_symlink():
            raise ValueError("Symlinked review output directory prohibited")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ("review-" + digest + ".json")
    if destination.exists() or destination.is_symlink():
        if destination.is_file() and destination.read_bytes() == payload:
            return str(destination)
        raise FileExistsError("Review receipt path collides with existing data")
    fd, tempname = tempfile.mkstemp(prefix=".partial-review-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(tempname, destination)
    finally:
        Path(tempname).unlink(missing_ok=True)
    return str(destination)


def _hash_original_file(path: Path) -> str:
    sha = sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save_gui_psf0(preview: dict, progress: Callable | None = None) -> str:
    """Explicit user-initiated diagnostic export with source-change detection."""
    if not isinstance(preview, dict) or not isinstance(preview.get("result"), PSF0Result):
        raise TypeError("Run a successful PSF-0 preview before saving")
    result = preview["result"]
    if result.lineage_id is None or not result.source_hashes:
        raise ValueError("Verified source hashes required before diagnostic save")
    project = _leo_p_project(preview["project_root"])
    if (project.project_id != preview["project_id"]
            or not _same_observation_source(project.selected_dataset, preview.get("dataset"))):
        raise ValueError("Galaxy project or selected dataset changed; rerun the diagnostic")
    release, region, brick, _ = project.selected_dataset
    if result.dataset_id != f"{release}/{region}/{brick}/{result.dataset_id.split('/')[-1]}":
        raise ValueError("PSF result does not match the Galaxy Workspace source")
    band = result.dataset_id.split("/")[-1]
    expected_paths = _expected_paths(project.data_root, (release, region, brick, band))
    _progress(progress, "Rechecking original FITS bytes against PSF source fingerprints")
    for kind in KINDS:
        p = expected_paths[kind]
        if not p.is_file() or p.is_symlink():
            raise ValueError(f"Original {kind.upper()} FITS missing/unsafe; cannot save")
        if _hash_original_file(p) != result.source_hashes[kind]:
            raise ValueError(f"Original {kind.upper()} FITS changed since PSF build; rerun")
    catalogue_sha = result.metrics.get(
        "catalog_selection_qa", {}).get("catalog_source_sha256")
    if catalogue_sha is not None:
        catalog_path, _ = tractor_catalog_location(
            project.data_root, release=release, region=region, brick=brick)
        if (not catalog_path.is_file() or catalog_path.is_symlink() or
                _hash_original_file(catalog_path) != catalogue_sha):
            raise ValueError(
                "Original DR11 Tractor/Gaia catalogue changed; cannot "
                "export a source-mismatched experimental PSF")
    _progress(progress, "Publishing immutable experimental PSF FITS + QA report")
    output = export_psf0_diagnostic(
        result, project.root / "analyses" / "psf-pilots", explicitly_requested=True)
    return str(output)
