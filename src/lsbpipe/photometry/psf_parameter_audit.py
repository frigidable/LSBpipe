"""Observational diagnostics for PSF-0 numerical choices (no science adoption).

Keep the original fitter and source-selection contract untouched.  These
diagnostics identify *which* choices are priors/heuristics and quantify
limited sensitivity using original native pixels.  None certifies that a
stellar sky annulus is empty, a catalogued object is an isolated star, or
the coadd noise covariance is known.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from collections import Counter

import numpy as np
from scipy.spatial import cKDTree


# Exhaustive registry: adding a PSF0Config field requires an explicit
# scientific/engineering classification, otherwise this audit fails closed.
# These tags are epistemic classifications, not endorsements of values.
_PARAM_BASIS = {
    "guessed_fwhm_pix": ("OBSERVATIONAL_PRIOR_OR_MANUAL", "PSFSIZE or manual detection width; not fitted ePSF FWHM"),
    "detection_sigma": ("HEURISTIC", "DAO kernel detection threshold; completeness/false detections uncalibrated"),
    "cutout_size_pix": ("GEOMETRY_FROM_HEURISTIC", "recipe-dependent stamp size; science wing extent uncalibrated"),
    "sky_annulus_inner_pix": ("HEURISTIC_GEOMETRY", "stellar sky inner boundary; star wing contamination unmeasured"),
    "sky_annulus_outer_pix": ("HEURISTIC_GEOMETRY", "stellar sky outer boundary; star wing contamination unmeasured"),
    "max_invalid_fraction": ("HEURISTIC", "whole-stamp invalid pixel tolerance; masked cores separately forbidden"),
    "min_neighbor_separation_pix": ("HEURISTIC", "DAO centroid separation; not a modeled neighbor contamination bound"),
    "min_train_stars": ("HEURISTIC", "fit viability floor, not precision or population sufficiency"),
    "min_holdout_stars": ("HEURISTIC", "disjoint validation floor, not statistical power certification"),
    "holdout_every": ("SPLIT_POLICY", "deterministic spatial split, not blind scientific validation"),
    "max_training_stars": ("ENGINEERING_BUDGET", "training cap; may truncate spatial or brightness diversity"),
    "max_iterations": ("SOLVER_BUDGET", "iteration limit, not convergence or PSF fidelity proof"),
    "wing_smoothing": ("ALGORITHM_POLICY", "Photutils model regularization, requires observational A/B"),
    "psf_oversampling": ("SAMPLING_POLICY", "native sampling presently fixed to one; assess measured FWHM"),
    "max_star_gradient_sigma": ("HEURISTIC", "local plane gradient rejection relative to correlated sky RMS"),
    "seed": ("REPRODUCIBILITY", "fixed random seed; not a scientific tuning parameter"),
    "excluded_training_star_ids": ("RESEARCHER_SELECTION", "explicit human reviewed train exclusions; never holdouts"),
    "star_catalog_policy": ("IDENTITY_POLICY", "Gaia/Tractor/DAO catalog provenance, not itself clean-star proof"),
    "training_outlier_passes": ("ALGORITHM_POLICY", "in-sample training-only trim budget; uncalibrated"),
}


# Snapshot of thresholds outside PSF0Config.  These values are NOT
# authoritative in isolation: update this audit and its tests whenever the
# owning algorithm changes.  A future typed registry should make those
# algorithms consume these definitions directly.
_SECONDARY_THRESHOLDS = {
    "global_detection_sky_clip_sigma": (3.0, "psf_empirical.py sigma_clipped_stats"),
    "global_detection_sky_clip_maxiters": (6, "psf_empirical.py sigma_clipped_stats"),
    "stellar_ring_clip_sigma": (3.5, "psf_empirical.py _star_cutout"),
    "stellar_ring_plane_fit_maxiters": (4, "psf_empirical.py _star_cutout"),
    "stellar_ring_min_initial_pixels": (35, "psf_empirical.py _star_cutout"),
    "stellar_ring_min_retained_pixels": (25, "psf_empirical.py _star_cutout"),
    "tractor_match_radius_pix": (2.5, "psf_tractor_gaia.py TractorGaiaMatcher"),
    "tractor_min_band_snr_diagonal": (20.0, "psf_tractor_gaia.py match"),
    "tractor_significant_neighbor_flux_ratio": (0.02, "psf_tractor_gaia.py match"),
    "tractor_significant_neighbor_min_snr_diagonal": (5.0, "psf_tractor_gaia.py match"),
    "star_axis_ratio_max": (1.5, "psf_star_quality.py assess_psf_star_cutout"),
    "star_width_ratio_min": (0.65, "psf_star_quality.py assess_sample_widths"),
    "star_width_ratio_max": (1.45, "psf_star_quality.py assess_sample_widths"),
    "signed_negative_abs_mass_max": (0.025, "psf_empirical.py signed-kernel rejection"),
    "builder_dao_min_separation_default_fwhm": (
        2.5, "photutils 3.0+ DAOStarFinder implicit default"),
    "fixed_scene_nuisance_source_budget": (
        18, "psf_joint_scene.py MAX_NUISANCE_SOURCES; engineering cap"),
    "fixed_scene_expanded_width_factor": (
        2, "psf_joint_scene.py; scene_width=2*kernel_width-1, geometry-only"),
    "fixed_scene_sky_polynomial_degree": (
        1, "psf_joint_scene.py; planar local background approximation"),
    "fixed_scene_subpixel_interpolation_order": (
        3, "psf_joint_scene.py scipy.ndimage.map_coordinates cubic"),
    "p15_extended_scene_nuisance_budget": (
        28, "psf_extended_scene.py MAX_NUISANCE_SOURCES; engineering cap"),
    "p15_extended_intrinsic_pixel_integration_grid": (
        3, "psf_extended_scene.py SUBPIXEL_GRID; analytic profile approximation"),
    "p15_exponential_half_light_bn": (
        1.6783469900166612, "psf_extended_scene.py SERSIC_B[n=1]"),
    "p15_devaucouleurs_half_light_bn": (
        7.669249442500804, "psf_extended_scene.py SERSIC_B[n=4]"),
    "p15d_morphology_radius_fractional_stress": (
        0.10, "psf_extended_scene.py; heuristic OAT radius stress, NOT catalog sigma"),
    "p15d_morphology_ellipticity_absolute_stress": (
        0.05, "psf_extended_scene.py; heuristic OAT ellipticity-norm stress, NOT catalog sigma"),
    "p15d_morphology_position_angle_degree_stress": (
        10.0, "psf_extended_scene.py; heuristic OAT PA stress, NOT catalog sigma"),
    "p15d_stressed_extended_sources_per_scene": (
        1, "psf_extended_scene.py; nearest rendered source by pre-fit distance and OBJID"),
    "p15e_analytic_subpixel_convergence_grids": (
        (3, 5, 7), "psf_extended_scene.py; same-source numerical quadrature only, no external Tractor calibration"),
}


def audit_psf0_parameters(config):
    """Describe exactly which thresholds were chosen, without upgrading them.

    No estimator can infer whether FWHM came from PSFSIZE or from the GUI
    merely from PSF0Config; mark its provenance unresolved until bound to
    verified map/catalog source evidence.
    """
    values = asdict(config)
    if set(values) != set(_PARAM_BASIS):
        raise ValueError("PSF parameter registry is incomplete or stale")
    entries = {
        key: {
            "value": list(value) if isinstance(value, tuple) else value,
            "basis": _PARAM_BASIS[key][0],
            "justification": _PARAM_BASIS[key][1],
            "calibrated_from_original_fits": False,
        }
        for key, value in values.items()
    }
    side = float(config.cutout_size_pix - 1)
    isolation = float(config.min_neighbor_separation_pix)
    ring_outer = float(config.sky_annulus_outer_pix)
    return {
        "schema": "desidera.psf0-parameter-provenance/1",
        "status": "EXPLICIT_PILOT_ASSUMPTIONS_NOT_SCIENCE_CALIBRATED",
        "parameters": entries,
        "secondary_code_threshold_snapshot": {
            key: {"value": value, "implemented_at": location,
                  "basis": "UNVALIDATED_HEURISTIC_OR_LIBRARY_DEFAULT",
                  "automatically_verified_against_code": False}
            for key, (value, location) in _SECONDARY_THRESHOLDS.items()
        },
        "geometry": {
            "sky_inner_over_guessed_fwhm":
                float(config.sky_annulus_inner_pix/config.guessed_fwhm_pix),
            "sky_outer_over_guessed_fwhm":
                float(ring_outer/config.guessed_fwhm_pix),
            "ring_circles_disjoint_at_isolation_distance":
                bool(isolation >= 2*ring_outer),
            # Two axis-aligned squares can overlap at diagonal separation
            # up to sqrt(2) times their side length.
            "square_stamps_can_overlap_at_isolation_distance":
                bool(isolation < math.sqrt(2.)*side),
            "dao_non_detection_is_not_evidence_of_no_neighbour": True,
            "geometric_nonoverlap_does_not_prove_clean_psf_wings": True,
        },
        "automatic_parameter_science_certification": False,
    }


def _robust_ring_plane(image, valid, radius, dx, dy, r_inner, r_outer):
    """Repeat the same four-step robust annular plane fit on original pixels."""
    pixels = valid & (radius >= r_inner) & (radius < r_outer)
    if int(pixels.sum()) < 35:
        return {"status": "INSUFFICIENT_RING_PIXELS", "n_pixels": int(pixels.sum())}
    basis = np.column_stack((np.ones(int(pixels.sum())),
                             dx[pixels], dy[pixels]))
    data = np.asarray(image[pixels], dtype=float)
    take = np.ones(len(data), dtype=bool)
    for _ in range(4):
        if int(take.sum()) < 25:
            return {"status": "INSUFFICIENT_ROBUST_RING_PIXELS"}
        coeff = np.linalg.lstsq(basis[take], data[take], rcond=None)[0]
        residual = data - basis @ coeff
        scatter = 1.4826*np.median(np.abs(residual[take] -
                                            np.median(residual[take])))
        if not math.isfinite(float(scatter)) or scatter <= 0:
            scatter = float(np.std(residual[take]))
        if not math.isfinite(float(scatter)) or scatter <= 0:
            return {"status": "NO_RING_SCATTER"}
        take = np.abs(residual-np.median(residual[take])) < 3.5*scatter
    if int(take.sum()) < 25:
        return {"status": "INSUFFICIENT_ROBUST_RING_PIXELS"}
    coeff = np.linalg.lstsq(basis[take], data[take], rcond=None)[0]
    return {
        "status": "FIT_FOR_SENSITIVITY_ONLY",
        "sky_at_star_center_nMgy_pix": float(coeff[0]),
        "gradient_x_nMgy_pix_per_pix": float(coeff[1]),
        "gradient_y_nMgy_pix_per_pix": float(coeff[2]),
        "n_original_ring_pixels": int(pixels.sum()),
        "n_retained_ring_pixels": int(take.sum()),
    }


def audit_star_sky_ring_sensitivity(
    image, valid, *, center_xy, inner_radius, outer_radius,
    reference_sky_nMgy_pix, reference_rms_nMgy_pix,
    guessed_fwhm_pix,
):
    """Sensitivity of local sky to INNER rings, not an unbiased sky estimate.

    Chosen annulus is the baseline, and two strictly interior disjoint
    intervals are refitted on the same *original*, not sky-subtracted,
    native stamp.  The outer side beyond the stamp is unavailable.  This
    intentionally refuses to infer a sky-free radius or PSF wings.
    """
    img = np.asarray(image, dtype=float)
    ok = np.asarray(valid, dtype=bool)
    if img.ndim != 2 or ok.shape != img.shape:
        raise ValueError("Native image and valid-mask shapes must agree")
    if not np.isfinite(img[ok]).all():
        raise ValueError("Nonfinite valid original stellar pixels")
    if (not all(math.isfinite(float(v)) for v in
                (*center_xy, inner_radius, outer_radius,
                 reference_sky_nMgy_pix, reference_rms_nMgy_pix,
                 guessed_fwhm_pix)) or not 2 < inner_radius < outer_radius
            or reference_rms_nMgy_pix <= 0 or guessed_fwhm_pix <= 0):
        raise ValueError("Invalid native annulus sensitivity geometry")
    yy, xx = np.indices(img.shape)
    dx = xx-float(center_xy[0])
    dy = yy-float(center_xy[1])
    rr = np.hypot(dx, dy)
    # Fractions are *explicit probe geometry*, not inferred physical
    # optimal values. Every change is recorded, no PSF cut is changed.
    probes = (
        ("interior_60_80_percent", .60*inner_radius, .80*inner_radius),
        ("interior_80_95_percent", .80*inner_radius, .95*inner_radius),
    )
    rows = []
    for name, rmin, rmax in probes:
        result = _robust_ring_plane(img, ok, rr, dx, dy, rmin, rmax)
        row = {"probe": name, "inner_radius_pix": float(rmin),
               "outer_radius_pix": float(rmax), **result}
        if result["status"] == "FIT_FOR_SENSITIVITY_ONLY":
            delta = float(result["sky_at_star_center_nMgy_pix"] -
                          reference_sky_nMgy_pix)
            row["sky_offset_vs_selected_ring_nMgy_pix"] = delta
            row["sky_offset_vs_local_rms"] = float(delta/reference_rms_nMgy_pix)
            core = ok & (rr <= guessed_fwhm_pix)
            row["uniform_offset_impact_in_core_aperture_nMgy"] = (
                float(delta*int(core.sum())))
            row["n_valid_core_pixels"] = int(core.sum())
        rows.append(row)
    deltas = [abs(row["sky_offset_vs_local_rms"]) for row in rows
              if "sky_offset_vs_local_rms" in row]

    # Do not infer the best ring from two hand-picked fractions. Scan
    # EVERY integer native-pixel inner boundary from the *guessed*
    # one-FWHM radius through the configured chosen inner edge while
    # keeping the originally observed outer boundary fixed. This is a
    # nested sensitivity curve, not statistically independent trials,
    # and not an automatic choice of a new ring. Each unsuccessful
    # plane fit remains explicit evidence rather than a silent omission.
    first_scan_radius = max(3, int(math.ceil(guessed_fwhm_pix)))
    last_scan_radius = int(math.floor(inner_radius))
    scan = []
    for candidate_inner in range(first_scan_radius, last_scan_radius+1):
        result = _robust_ring_plane(
            img, ok, rr, dx, dy, float(candidate_inner), float(outer_radius))
        item = {"inner_boundary_pix": candidate_inner,
                "outer_boundary_pix": float(outer_radius), **result}
        if result["status"] == "FIT_FOR_SENSITIVITY_ONLY":
            delta = float(result["sky_at_star_center_nMgy_pix"] -
                          reference_sky_nMgy_pix)
            item["sky_offset_vs_selected_ring_nMgy_pix"] = delta
            item["sky_offset_vs_local_rms"] = float(
                delta/reference_rms_nMgy_pix)
        scan.append(item)

    return {
        "schema": "desidera.psf0-stellar-annulus-sensitivity/1",
        "status": "ORIGINAL_PIXELS_DESCRIPTIVE_NOT_A_SKY_CALIBRATION",
        "chosen_ring_inner_pix": float(inner_radius),
        "chosen_ring_outer_pix": float(outer_radius),
        "chosen_ring_sky_at_center_nMgy_pix": float(reference_sky_nMgy_pix),
        "chosen_ring_local_rms_nMgy_pix": float(reference_rms_nMgy_pix),
        "probes": rows,
        "one_pixel_inner_boundary_sensitivity_scan": scan,
        "scan_uses_guessed_not_measured_fwhm_for_start": True,
        "scan_step_native_pix": 1,
        "scan_sky_outer_boundary_fixed": True,
        "scan_entries_nested_and_not_statistically_independent": True,
        "scan_not_an_automatic_sky_ring_optimizer": True,
        "max_absolute_inner_ring_sky_change_in_local_rms":
            max(deltas) if deltas else None,
        "outer_ring_beyond_cutout_not_tested": True,
        "rings_overlap_correlated_pixels_or_share_background_structure": True,
        "could_be_stellar_wings_or_background_variation": True,
        "coadd_noise_covariance_unmodeled": True,
        "changes_psf_selection_or_sky_model": False,
        "scientific_adoption_permitted": False,
    }


def audit_gaia_catalog_seeds_vs_dao(
    catalog_matcher, dao_positions, *,
    frame_shape, protected, band, cutout_size_pix,
    guessed_fwhm_pix,
):
    """Count Gaia/Tractor PSF candidates lost by the DAO first-stage gate.

    Catalog position and DAO centroid proximity are distinct observations.
    Catalog-vetted *potential* seeds are NOT cutout-screened stars; in
    particular they cannot be promoted into training or heldout samples
    from this report.  The original DAO selection remains unchanged.
    """
    xy = np.asarray(dao_positions, dtype=float)
    if xy.size == 0:
        xy = np.empty((0, 2), dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
        raise ValueError("DAO positions must be finite Nx2")
    if len(frame_shape) != 2 or protected.shape != tuple(frame_shape):
        raise ValueError("Protected image and native image shape must agree")
    search = cKDTree(xy) if len(xy) else None
    ny, nx = frame_shape
    half = cutout_size_pix//2
    missed = []
    rejection_counts = Counter()
    audited = 0
    vetted = 0
    with_dao = 0
    IDs = set()
    radius = min(50., max(3.*guessed_fwhm_pix, .65*cutout_size_pix))
    for row, pos in zip(catalog_matcher.rows, catalog_matcher.pixel_positions):
        if row.get("ref_cat") != "G3" or row.get("type") != "PSF" or row.get("ref_id", 0) <= 0:
            continue
        audited += 1
        gid = int(row["ref_id"])
        if gid in IDs:
            rejection_counts["DUPLICATE_GAIA_SOURCE_ID"] += 1
            continue
        IDs.add(gid)
        x, y = float(pos[0]), float(pos[1])
        if (not math.isfinite(x) or not math.isfinite(y)
                or x-half < 0 or y-half < 0 or
                x+half >= nx or y+half >= ny):
            rejection_counts["CUTOUT_OUTSIDE_NATIVE_IMAGE"] += 1
            continue
        if protected[int(round(y)), int(round(x))]:
            rejection_counts["GALAXY_PROTECTED_AT_CATALOG_SEED"] += 1
            continue
        identity, failure = catalog_matcher.match(
            x, y, band=band,
            neighbor_search_radius_pix=radius, require_gaia=True)
        if failure is not None:
            rejection_counts[failure] += 1
            continue
        vetted += 1
        distance = (float(search.query([x, y], k=1)[0])
                    if search is not None else None)
        # This is only a centroid proximity diagnostic, never an
        # unambiguous photometric association or stellar acceptance.
        if distance is not None and distance <= catalog_matcher.match_radius_pix:
            with_dao += 1
        else:
            missed.append({
                "gaia_source_id": gid, "x_pix": x, "y_pix": y,
                "nearest_dao_centroid_pix": distance,
                "catalog_flux_quality_passed": True,
                "native_cutout_and_sky_unchecked": True,
            })
    return {
        "schema": "desidera.psf0-gaia-catalog-seed-vs-dao/1",
        "status": "DIAGNOSTIC_NO_STELLAR_PROMOTION",
        "n_catalog_gaia_type_psf_rows": audited,
        "n_catalog_only_preflight_pass": vetted,
        "n_near_dao_centroid_not_proven_associated": with_dao,
        "n_catalog_preflight_without_near_dao": len(missed),
        "catalog_preflight_without_dao_details": missed,
        "preflight_rejected_reasons": dict(sorted(rejection_counts.items())),
        "dao_match_distance_pix_heuristic_not_astrometric_sigma":
            float(catalog_matcher.match_radius_pix),
        "dao_default_min_separation_may_hide_nearby_peaks": True,
        "catalog_only_candidates_have_not_passed_original_pixel_qa": True,
        "no_changes_to_stellar_sample_or_holdouts": True,
        "science_adopted": False,
    }
