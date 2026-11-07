"""Non-adopted, independent-star signed/final ePSF residual and growth QA.

A model that passes a global pixel-sign test is not necessarily a
better physical PSF. Here a signed, unmodified ePSF is amplitude-fitted
ONLY in each independent held-out star's core and evaluated in three
radial zones, including the outer cutout used for background modelling.

These are diagnostic residuals divided by observed stellar peak, NOT
noise-whitened likelihoods, PSF-wing validation or a calibrated error
budget. Original pixels and signed negative model components are intact.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.ndimage import shift as shift_image



# Fixed radii are based on the FINDER FWHM prior, not measured PSF width.
# The largest radius remains inside the extended-sky annulus (>=4.5 FWHM).
_GROWTH_RADII_FWHM = (1.0, 1.5, 2.0, 3.0, 4.0)


def _core_normalized_growth(science, model, valid, radius, *,
                            center_xy, guessed_fwhm_pix, amplitude,
                            local_sky_rms_nMgy_pix=None):
    """Compare cumulative native *sky-subtracted* flux on identical pixels.

    This is a finite-aperture flux-growth diagnostic, NOT enclosed energy
    relative to the unknown star's total flux. Both observed and modeled
    curves are divided by their OWN measured/modeled flux inside 1 guessed
    FWHM, so the free core amplitude cannot conceal a wing *shape* excess.
    No flux extrapolation, mask inpainting, covariance correction or PSF
    science acceptance is performed.
    """
    cy_size, cx_size = science.shape
    cx, cy = center_xy
    rcore = float(guessed_fwhm_pix)
    core = valid & (radius <= rcore)
    core_observed = float(np.sum(science[core]))
    core_model = float(np.sum(amplitude * model[core]))
    valid_core_normalization = (
        math.isfinite(core_observed) and core_observed > 0
        and math.isfinite(core_model) and core_model > 0)
    points = []
    for factor in _GROWTH_RADII_FWHM:
        r = float(factor*rcore)
        contained = (
            cx-r >= 0 and cy-r >= 0
            and cx+r <= cx_size-1 and cy+r <= cy_size-1)
        disk = radius <= r
        selected = disk & valid
        n_total = int(np.count_nonzero(disk))
        n_valid = int(np.count_nonzero(selected))
        fully_valid = n_total > 0 and n_valid == n_total
        if not contained:
            status = "NOT_FULLY_CONTAINED"
        elif not fully_valid:
            status = "MASKED_APERTURE_DIAGNOSTIC_ONLY"
        elif not valid_core_normalization:
            status = "INVALID_CORE_REFERENCE"
        else:
            status = "FULL_NATIVE_APERTURE"
        # Never describe a truncated aperture or nonpositive core
        # normalization as a physical flux growth measurement.
        usable = contained and valid_core_normalization
        obs = (float(np.sum(science[selected])) / core_observed
               if usable else None)
        pred = (float(np.sum(amplitude*model[selected])) / core_model
                if usable else None)
        # Algebraic sensitivity: a UNIFORM additive correction to the
        # already-subtracted stellar image that would make its observed
        # core-normalized growth equal the model at this specific radius.
        # Solving (F_r - delta*N_r)/(F_core - delta*N_core) = M_r/M_core.
        # This is NOT a fitted sky correction, error bar or significance,
        # and different radii imply correlated, possibly incompatible
        # corrections. Never apply it to stellar pixels or PSF reconstruction.
        equivalent = None
        equivalent_over_rms = None
        if (status == "FULL_NATIVE_APERTURE" and factor > 1.
                and obs is not None and pred is not None):
            ncore = int(np.count_nonzero(core))
            denom = float(n_valid - pred*ncore)
            if abs(denom) > 1.e-12 and math.isfinite(denom):
                candidate = float(core_observed*(obs-pred)/denom)
                if math.isfinite(candidate):
                    equivalent = candidate
                    if (local_sky_rms_nMgy_pix is not None
                            and math.isfinite(local_sky_rms_nMgy_pix)
                            and local_sky_rms_nMgy_pix > 0):
                        equivalent_over_rms = float(
                            candidate/local_sky_rms_nMgy_pix)
        points.append({
            "equivalent_uniform_sky_offset_nMgy_per_native_pixel": equivalent,
            "equivalent_uniform_sky_offset_over_local_pixel_rms": equivalent_over_rms,
            "equivalent_sky_offset_is_not_an_inferred_true_sky_bias": True,
            "radius_in_guessed_fwhm": float(factor),
            "radius_native_pix": r,
            "status": status,
            "n_disk_native_pixels": n_total,
            "n_valid_native_pixels": n_valid,
            "valid_pixel_fraction": (
                float(n_valid/n_total) if n_total else None),
            "observed_cumulative_flux_over_observed_core": obs,
            "model_cumulative_flux_over_model_core": pred,
            "observed_minus_model_growth_over_core": (
                float(obs-pred) if obs is not None and pred is not None
                else None),
        })
    # A true uniform sky offset would predict a single correction in
    # EVERY complete aperture. The observed span only measures radial
    # inconsistency of that restricted hypothesis; it is NOT a chi-square
    # or rejection significance without covariance and sky-plane uncertainty.
    sky_rows = [
        p for p in points
        if p["radius_in_guessed_fwhm"] > 1.
        and p["status"] == "FULL_NATIVE_APERTURE"
        and p["equivalent_uniform_sky_offset_nMgy_per_native_pixel"] is not None
    ]
    offsets = [
        p["equivalent_uniform_sky_offset_nMgy_per_native_pixel"]
        for p in sky_rows
    ]
    eq_span = (
        float(max(offsets) - min(offsets)) if len(offsets) >= 2 else None
    )
    eq_span_over_rms = (
        float(eq_span / local_sky_rms_nMgy_pix)
        if eq_span is not None and local_sky_rms_nMgy_pix is not None
        and math.isfinite(local_sky_rms_nMgy_pix)
        and local_sky_rms_nMgy_pix > 0 else None
    )
    return {
        "radial_sky_equivalence_consistency": {
            "schema": "desidera.psf0-radial-sky-equivalence/1",
            "status": (
                "RADIAL_SENSITIVITY_ONLY" if eq_span is not None
                else "INSUFFICIENT_COMPLETE_APERTURES"
            ),
            "tested_radii_in_guessed_fwhm": [
                p["radius_in_guessed_fwhm"] for p in sky_rows
            ],
            "n_usable_radii_above_core": len(sky_rows),
            "span_of_equivalent_offsets_nMgy_per_native_pixel": eq_span,
            "span_over_local_pixel_rms": eq_span_over_rms,
            "constant_sky_cannot_be_tested_without_covariance": True,
            "radii_cumulative_and_not_statistically_independent": True,
            "not_a_chi_square_or_statistical_significance": True,
            "no_sky_or_psf_correction_applied": True,
        },
        "schema": "desidera.psf0-holdout-core-normalized-flux-growth/2",
        "status": "DESCRIPTIVE_NOT_SCIENCE_ADOPTED",
        "core_radius_guessed_fwhm": 1.0,
        "core_reference_valid": bool(valid_core_normalization),
        "core_observed_native_flux_nMgy": (
            core_observed if valid_core_normalization else None),
        "core_fitted_model_native_flux_nMgy": (
            core_model if valid_core_normalization else None),
        "radii": points,
        "all_radii_cumulative_and_not_statistically_independent": True,
        "observed_cutout_local_sky_already_subtracted": True,
        "masked_apertures_not_usable_for_unbiased_growth": True,
        "stellar_total_flux_unmeasured": True,
        "coadd_covariance_and_sky_uncertainty_unmodeled": True,
        "uniform_sky_equivalent_uses_each_radius_separately": True,
        "uniform_sky_equivalent_is_algebra_not_sky_calibration": True,
        "source_pixels_not_modified": True,
    }


def summarize_core_normalized_growth(reports: list[dict]) -> dict:
    """Across fixed holdouts, summarize ONLY fully contained unmasked disks."""
    # The primary signed/final holdout integrity gate checks duplicate IDs;
    # enforce it independently here so no caller can inflate the sample.
    ids = [r["source_id"] for r in reports]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate holdout source IDs in aperture-growth QA")
    entries = []
    for factor in _GROWTH_RADII_FWHM:
        usable = []
        for report in reports:
            points = report.get("core_normalized_aperture_growth", {}).get(
                "radii", [])
            point = next((x for x in points
                          if x.get("radius_in_guessed_fwhm") == factor), None)
            if point is not None and point.get("status") == "FULL_NATIVE_APERTURE":
                excess = point.get("observed_minus_model_growth_over_core")
                if excess is not None and math.isfinite(float(excess)):
                    usable.append((report["source_id"], float(excess)))
        numbers = [v for _, v in usable]
        entries.append({
            "radius_in_guessed_fwhm": factor,
            "n_full_aperture_holdouts": len(numbers),
            "source_ids": [s for s, _ in usable],
            "median_observed_minus_model_growth_over_core": (
                float(np.median(numbers)) if numbers else None),
            "median_absolute_growth_excess_over_core": (
                float(np.median(np.abs(numbers))) if numbers else None),
        })
    return {
        "schema": "desidera.psf0-holdout-flux-growth-summary/1",
        "status": "DESCRIPTIVE_NOT_SCIENCE_ADOPTED",
        "n_fixed_holdouts": len(reports),
        "radii": entries,
        "sky_and_coadd_covariance_not_calibrated": True,
        "no_threshold_or_science_adoption_implication": True,
    }



def heldout_signed_residual(
    *,
    source_id: str,
    image: np.ndarray,
    sqrt_ivar: np.ndarray,
    signed_kernel: np.ndarray,
    local_center_xy: tuple[float, float],
    guessed_fwhm_pix: float,
    measured_peak: float,
    local_sky_rms_nMgy_pix: float | None = None,
) -> dict:
    """Fit core-only amplitude and inspect signed model residual by annulus."""
    science = np.asarray(image, dtype=float)
    weights = np.asarray(sqrt_ivar, dtype=float)
    kernel = np.asarray(signed_kernel, dtype=float)
    if (science.ndim != 2 or science.shape != kernel.shape
            or science.shape != weights.shape or min(science.shape) < 15
            or not np.all(np.isfinite(science))
            or not np.all(np.isfinite(weights))
            or np.any(weights < 0)
            or not np.all(np.isfinite(kernel))):
        raise ValueError("Signed holdout inputs must share a finite native-grid shape")
    if (len(local_center_xy) != 2
            or not all(math.isfinite(float(v)) for v in local_center_xy)
            or not math.isfinite(guessed_fwhm_pix)
            or guessed_fwhm_pix <= 0
            or not math.isfinite(measured_peak)
            or measured_peak <= 0):
        raise ValueError("Signed holdout requires valid center, FWHM guess and peak")

    cx0, cy0 = (np.array(kernel.shape[::-1])-1)/2
    cx, cy = map(float, local_center_xy)
    model = shift_image(
        kernel, (cy-cy0, cx-cx0), order=3, mode="constant",
        cval=0., prefilter=True)
    rr_y, rr_x = np.indices(science.shape, dtype=float)
    radius = np.hypot(rr_x-cx, rr_y-cy)
    good = weights > 0
    core = good & (radius <= float(guessed_fwhm_pix))
    if np.count_nonzero(core) < 5:
        raise ValueError("Too few independent holdout core pixels")
    w2 = weights[core] ** 2
    denominator = float(np.sum(w2 * model[core]**2))
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("Cannot amplitude-fit signed model in holdout core")
    amplitude = float(np.sum(w2 * model[core] * science[core]) / denominator)
    if not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("Signed holdout core amplitude is not positive")
    residual = science - amplitude*model
    annuli = {
        "core_r_le_1x_guessed_fwhm": core,
        "intermediate_1x_to_2x_guessed_fwhm": (
            good & (radius > guessed_fwhm_pix) &
            (radius <= 2*guessed_fwhm_pix)),
        "outer_r_gt_2x_guessed_fwhm": (
            good & (radius > 2*guessed_fwhm_pix)),
    }
    metrics = {}
    for name, region in annuli.items():
        values = residual[region]/measured_peak
        metrics[name] = {
            "n_valid_pixels": int(values.size),
            "median_signed_residual_over_peak": (
                float(np.median(values)) if values.size else None),
            "median_absolute_residual_over_peak": (
                float(np.median(np.abs(values))) if values.size else None),
        }
    return {
        "source_id": str(source_id),
        "fitted_amplitude_model_units": amplitude,
        "radial_residuals": metrics,
        "core_normalized_aperture_growth": _core_normalized_growth(
            science, model, good, radius,
            center_xy=(cx, cy), guessed_fwhm_pix=guessed_fwhm_pix,
            amplitude=amplitude,
            local_sky_rms_nMgy_pix=local_sky_rms_nMgy_pix),
        "fwhm_guess_not_measured": float(guessed_fwhm_pix),
        "signed_kernel_unchanged": True,
        "correlated_coadd_noise_not_whitened": True,
    }


def summarize_signed_holdouts(reports: list[dict]) -> dict:
    """Report independent-star summaries without inventing precision."""
    names = (
        "core_r_le_1x_guessed_fwhm",
        "intermediate_1x_to_2x_guessed_fwhm",
        "outer_r_gt_2x_guessed_fwhm",
    )
    # Repeated reports from one physical star are not independent trials.
    # This is an integrity guard, not a certification of distinct source IDs
    # as astrophysically independent stars or survey exposures.
    seen_ids: set[str] = set()
    for row in reports:
        if not isinstance(row, dict):
            raise TypeError("Each signed holdout report must be a mapping")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("Each holdout report requires a nonempty source_id")
        source_id = source_id.strip()
        if source_id in seen_ids:
            raise ValueError(
                f"Duplicate holdout source_id cannot count as an independent star: {source_id}")
        seen_ids.add(source_id)
    result = {"n_usable_holdout_stars": len(seen_ids), "regions": {}}
    for name in names:
        signed = []
        absolute = []
        for row in reports:
            zone = row["radial_residuals"][name]
            if zone["n_valid_pixels"] < 4:
                continue
            if zone["median_signed_residual_over_peak"] is None:
                continue
            signed.append(zone["median_signed_residual_over_peak"])
            absolute.append(zone["median_absolute_residual_over_peak"])
        result["regions"][name] = {
            "n_stars": len(signed),
            "median_star_signed_residual_over_peak": (
                float(np.median(signed)) if signed else None),
            "median_star_abs_residual_over_peak": (
                float(np.median(absolute)) if absolute else None),
        }
    return result


def audit_signed_holdout_coverage(expected_source_ids, reports, failures) -> dict:
    """Check EVERY fixed held-out star and all three annuli, without resampling.

    This is a fail-closed integrity gate, not a statistical goodness-of-fit
    criterion. A failed/blank stellar residual must not silently reduce
    the denominator of the independent validation sample.
    """
    expected = tuple(str(s) for s in expected_source_ids)
    if not expected or len(set(expected)) != len(expected) or any(
            not s.strip() for s in expected):
        raise ValueError("Holdout inventory must have unique nonblank IDs")
    returned = [item["source_id"] for item in reports]
    failed = [item["star_id"] for item in failures]
    if (len(set(returned)) != len(returned)
            or len(set(failed)) != len(failed)
            or (set(returned) | set(failed)) - set(expected)
            or set(returned) & set(failed)):
        raise ValueError("Duplicate, foreign or overlapping held-out star IDs")
    annuli = (
        "core_r_le_1x_guessed_fwhm",
        "intermediate_1x_to_2x_guessed_fwhm",
        "outer_r_gt_2x_guessed_fwhm",
    )
    incomplete = {}
    for report in reports:
        for name in annuli:
            zone = report.get("radial_residuals", {}).get(name, {})
            n = zone.get("n_valid_pixels", 0)
            signed = zone.get("median_signed_residual_over_peak")
            absolute = zone.get("median_absolute_residual_over_peak")
            if (not isinstance(n, (int, np.integer)) or n < 4 or
                    signed is None or absolute is None or
                    not math.isfinite(float(signed)) or
                    not math.isfinite(float(absolute))):
                incomplete.setdefault(report["source_id"], []).append(name)
    missing = sorted(set(expected) - set(returned) - set(failed))
    complete = (not missing and not failed and not incomplete
                and len(reports) == len(expected))
    return {
        "schema": "desidera.psf0-holdout-coverage/1",
        "status": "COMPLETE" if complete else "INCOMPLETE_REJECTED",
        "expected_source_ids": list(expected),
        "n_expected_holdout_stars": len(expected),
        "n_successfully_scored_stars": len(reports),
        "failed_source_ids": sorted(failed),
        "missing_source_ids": missing,
        "incomplete_annuli_by_source_id": incomplete,
        "min_valid_native_pixels_per_annulus": 4,
        "all_fixed_holdouts_and_annuli_scored": bool(complete),
        "not_covariance_calibrated": True,
        "holdout_scores_do_not_select_training_stars": True,
    }

def heldout_final_kernel_residual(
    *,
    source_id: str,
    image: np.ndarray,
    sqrt_ivar: np.ndarray,
    final_kernel: np.ndarray,
    local_center_xy: tuple[float, float],
    guessed_fwhm_pix: float,
    measured_peak: float,
    local_sky_rms_nMgy_pix: float | None = None,
) -> dict:
    """Score the ACTUAL nonnegative, normalized delivered kernel on fixed holdouts.

    The three radial masks, core-only weighted amplitude fit, shift interpolation
    and peak-normalized residual metric are identical to the signed-model audit.
    This is an independent-star *diagnostic*, not wing validation or calibrated
    goodness-of-fit. Never convert an invalid signed model to a pass here.
    """
    kernel = np.asarray(final_kernel, dtype=float)
    if (kernel.ndim != 2 or not np.all(np.isfinite(kernel))
            or np.any(kernel < 0.) or
            not np.isclose(float(np.sum(kernel)), 1., rtol=0., atol=1e-8)):
        raise ValueError("Final holdout kernel must be finite, nonnegative and normalized")
    # Reuse precisely the same fitting/radial scoring mathematics as the
    # signed precursor so discrepancies can be attributed to the kernel
    # transformation rather than to a different comparison procedure.
    qa = heldout_signed_residual(
        source_id=source_id, image=image, sqrt_ivar=sqrt_ivar,
        signed_kernel=kernel, local_center_xy=local_center_xy,
        guessed_fwhm_pix=guessed_fwhm_pix, measured_peak=measured_peak,
        local_sky_rms_nMgy_pix=local_sky_rms_nMgy_pix)
    qa.pop("signed_kernel_unchanged")
    qa["kernel_representation"] = "FINAL_NONNEGATIVE_NORMALIZED"
    qa["final_kernel_nonnegative_and_unit_sum"] = True
    qa["evaluated_on_identical_native_holdout_pixels"] = True
    return qa
