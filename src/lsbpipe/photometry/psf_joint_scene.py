"""Read-only native coadd joint scene decomposition around fixed PSF-0 stars.

This pilot *holds the already built empirical PSF kernel fixed* and solves
simultaneously for the central-star amplitude, compatible catalogued point
source amplitudes and a planar sky on a larger region of ORIGINAL coadd
pixels. It does not recover PSF flux beyond the empirical kernel support
or account for extended catalogued objects or correlated coadd covariance.

Neither a better residual nor a nonnegative fitted neighbor demonstrates
scientific PSF validation. No fit output feeds back into training, holdouts,
star rejection, sky subtraction, the target, or the adopted PSF state.
"""
from __future__ import annotations

from hashlib import sha256
import math

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.optimize import nnls
from scipy.spatial import cKDTree


SCHEMA = "desidera.psf0-fixed-psf-joint-native-scene/3"
MAX_NUISANCE_SOURCES = 18  # engineering compute budget, NOT science calibration


def _finite_float(value):
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _shifted_native_kernel(kernel, xx, yy, x, y):
    """Sample frozen native PSF at exact subpixel Tractor/native coordinates."""
    kh, kw = kernel.shape
    coords = np.array([yy-y+(kh-1)/2., xx-x+(kw-1)/2.])
    # Cubic interpolation can introduce small negative numerical lobes.
    # The delivered PSF0 kernel is nonnegative; this diagnostic explicitly
    # uses a nonnegative basis without modifying the original kernel.
    return np.maximum(map_coordinates(
        kernel, coords, order=3, mode="constant", cval=0.,
        prefilter=True), 0.)


def _projected_nnls(y, B, P, w):
    """Solve an unpenalized free sky and nonnegative point-source fluxes.

    Project away the *unconstrained* three sky coefficients by QR, then
    solve NNLS for ALL point sources. This avoids arbitrary Tikhonov
    regularization or fitting sky to a pre-subtracted stellar image.
    """
    y = np.asarray(y, dtype=float)
    w = np.asarray(w, dtype=float)
    Bw = B*w[:, None]
    Pw = P*w[:, None]
    yw = y*w
    if len(y) <= P.shape[1]+3:
        raise ValueError("INSUFFICIENT_SCENE_PIXELS_FOR_JOINT_FIT")
    q, r = np.linalg.qr(Bw, mode="reduced")
    if np.linalg.matrix_rank(r) < 3:
        raise ValueError("DEGENERATE_SCENE_SKY_GEOMETRY")
    projected = Pw-q@(q.T@Pw)
    data_projected = yw-q@(q.T@yw)
    singular = np.linalg.svd(projected, compute_uv=False)
    if (not np.isfinite(singular).all()
            or not len(singular) or singular[-1] <=
            np.finfo(float).eps*max(projected.shape)*singular[0]):
        raise ValueError("NON_IDENTIFIABLE_SCENE_SOURCE_TEMPLATES")
    amps, _ = nnls(projected, data_projected)
    sky_coeff = np.linalg.lstsq(Bw, yw-Pw@amps, rcond=None)[0]
    if not np.isfinite(amps).all() or not np.isfinite(sky_coeff).all():
        raise ValueError("NONFINITE_JOINT_SCENE_SOLUTION")
    resid = yw-Bw@sky_coeff-Pw@amps
    return amps, sky_coeff, float(resid@resid), float(singular[0]/singular[-1])


def fit_fixed_psf_scene(
    original_image, original_invvar, original_valid, *,
    kernel, target_xy, neighbors, guessed_fwhm_pix, original_sky_nMgy_pix,
    scene_size_pix=None,
):
    """Fit star+plane+catalog PSF neighbors on actual, unmodified pixels.

    The patch width defaults to exactly twice the empirical PSF's linear
    pixel support minus one (81 -> 161), a declared *engineering probe*
    geometry, not a measured sky-free boundary. It is bounded by the
    input native patch; callers must not secretly pad or inpaint edges.
    """
    data = np.asarray(original_image, dtype=float)
    ivar = np.asarray(original_invvar, dtype=float)
    valid = np.asarray(original_valid, dtype=bool)
    k = np.asarray(kernel, dtype=float)
    if (data.ndim != 2 or data.shape != ivar.shape or data.shape != valid.shape
            or k.ndim != 2 or k.shape[0] != k.shape[1]
            or k.shape[0] % 2 != 1 or k.shape[0] < 15
            or not np.isfinite(k).all() or np.any(k < 0)
            or not math.isclose(float(np.sum(k)), 1., rel_tol=1e-8, abs_tol=1e-8)):
        raise ValueError("Invalid original native arrays or frozen PSF")
    width = 2*k.shape[0]-1 if scene_size_pix is None else scene_size_pix
    if (type(width) is not int or width < k.shape[0]
            or width > 2*k.shape[0]-1 or width % 2 == 0):
        raise ValueError("Invalid bounded native scene footprint")
    if (len(target_xy) != 2 or
            any(_finite_float(v) is None for v in target_xy) or
            _finite_float(guessed_fwhm_pix) is None or guessed_fwhm_pix <= 0 or
            _finite_float(original_sky_nMgy_pix) is None):
        raise ValueError("Invalid scene coordinates or native sky reference")
    if len(neighbors) > MAX_NUISANCE_SOURCES:
        return {
            "status": "TOO_MANY_PSF_NEIGHBORS_FOR_BOUNDED_FIT",
            "n_catalog_point_neighbors": len(neighbors),
            "science_adopted": False,
        }
    cx, cy = map(float, target_xy)
    half = width//2
    x0, y0 = int(np.rint(cx))-half, int(np.rint(cy))-half
    if x0 < 0 or y0 < 0 or x0+width > data.shape[1] or y0+width > data.shape[0]:
        return {"status": "FULL_EXPANDED_NATIVE_STAMP_OUTSIDE_IMAGE",
                "scene_width_pix": width, "science_adopted": False}
    rows = slice(y0, y0+width)
    cols = slice(x0, x0+width)
    z = data[rows, cols]
    inv = ivar[rows, cols]
    m = valid[rows, cols] & np.isfinite(z) & np.isfinite(inv) & (inv > 0)
    if not m[half, half]:
        return {"status": "INVALID_CENTRAL_NATIVE_PIXEL",
                "science_adopted": False}
    n_valid = int(np.count_nonzero(m))
    # Only impose the mathematical number-of-unknowns requirement;
    # no uncalibrated 25%-valid-stamp acceptance threshold is invented.
    # The QR/SVD tests below check the actual numerical rank.
    if n_valid <= len(neighbors) + 3:
        return {"status": "INSUFFICIENT_NATIVE_SCENE_PIXEL_DOF",
                "n_valid": n_valid, "n_total": width*width,
                "n_point_source_coefficients": len(neighbors)+1,
                "n_planar_sky_coefficients": 3,
                "science_adopted": False}
    gy, gx = np.indices(z.shape, dtype=float)
    gx += x0
    gy += y0
    extent = max(1., float(half))
    B = np.column_stack((
        np.ones(n_valid),
        ((gx-cx)/extent)[m],
        ((gy-cy)/extent)[m],
    ))
    center_template = _shifted_native_kernel(k, gx, gy, cx, cy)
    templates = [center_template]
    active_neighbors = []
    zero_support_neighbors = []
    # A catalog position can be inside the *approximate* patch+kernel
    # bounding rectangle but its interpolated finite-support template can
    # have EXACTLY zero usable native pixels (especially near corners and
    # masks). This is an identically zero design-matrix column, NOT a
    # mathematical degeneracy of the central star. Exclude it explicitly
    # with evidence; never silently discard a source with any model support.
    for row in neighbors:
        x, y = float(row["x_pix"]), float(row["y_pix"])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Nonfinite neighbor catalogue coordinate")
        template = _shifted_native_kernel(k, gx, gy, x, y)
        if not np.any(template[m] > 0.):
            zero_support_neighbors.append({
                "tractor_objid": int(row["tractor_objid"]),
                "separation_from_star_pix": float(math.hypot(x-cx, y-cy)),
                "status": "EXACT_ZERO_FROZEN_KERNEL_ON_ALL_VALID_SCENE_PIXELS",
            })
            continue
        active_neighbors.append(row)
        templates.append(template)
    columns = []
    template_peak_scales = []
    for temp in templates:
        t = temp[m]
        maximum = float(np.max(t)) if len(t) else 0.
        if maximum <= 0 or not math.isfinite(maximum):
            return {"status": "CENTRAL_OR_ACTIVE_TEMPLATE_HAS_NO_VALID_SUPPORT",
                    "science_adopted": False}
        columns.append(t/maximum)
        template_peak_scales.append(maximum)
    P = np.column_stack(columns)
    weight = np.sqrt(inv[m])
    try:
        amplitudes, sky, full_rss, condition = _projected_nnls(
            z[m], B, P, weight)
        null_amplitudes, null_sky, null_rss, null_condition = _projected_nnls(
            z[m], B, P[:, :1], weight)
    except (ValueError, RuntimeError) as exc:
        return {
            "status": "JOINT_SCENE_NUMERICALLY_UNIDENTIFIABLE",
            "reason": str(exc), "n_valid": n_valid,
            "n_catalog_point_neighbors": len(neighbors),
            "science_adopted": False,
        }
    if amplitudes[0] <= 0 or not math.isfinite(float(amplitudes[0])):
        return {
            "status": "NONPOSITIVE_FITTED_CENTRAL_STAR_AMPLITUDE",
            "n_valid": n_valid, "n_catalog_point_neighbors": len(neighbors),
            "science_adopted": False,
        }
    # Full and null fits use the *identical original native pixels*.
    # A nested fit is guaranteed to be at least as good. A reduced RSS
    # is NOT a significance, p-value, evidence ratio or true-PSF metric.
    r2 = np.hypot(gx-cx, gy-cy)
    within_core = m & (r2 <= guessed_fwhm_pix)
    within_outer = m & (r2 <= 4*guessed_fwhm_pix)
    # Global residuals can be dominated by other bright stars near the
    # edge of this 161px scene. Separately compare the IDENTICAL central
    # apertures, still using amplitudes fitted globally in both models.
    # Local residual improvements may be NEGATIVE: never clamp them.
    full_residual = (z[m] - B@sky - P@amplitudes) * weight
    null_residual = (z[m] - B@null_sky - P[:, :1]@null_amplitudes)*weight
    native_radius = r2[m]
    # The full-minus-null image model is an exact sum of THREE changes:
    # change in the central star amplitude, change in the free sky plane,
    # and added catalog point-neighbor templates. This is a descriptive
    # decomposition of fitted model changes, NOT astrophysical causation.
    model_changes = {
        "central_star_amplitude_reallocation": (
            weight * P[:, 0] * (amplitudes[0]-null_amplitudes[0])),
        "planar_sky_reallocation": (
            weight * (B @ (sky-null_sky))),
        "direct_catalog_point_neighbors": (
            weight * (P[:, 1:] @ amplitudes[1:])),
    }
    total_model_change = sum(model_changes.values())
    def radial_residual_comparison(radius_pix):
        aperture = (np.ones(len(native_radius), dtype=bool)
                    if radius_pix is None else native_radius <= radius_pix)
        if not np.any(aperture):
            return {
                "status": "NO_VALID_PIXELS_IN_APERTURE",
                "n_native_pixels": 0,
                "radius_pix": float(radius_pix),
                "weighted_squared_residual_full": None,
                "weighted_squared_residual_star_plane_only": None,
                "signed_improvement_fraction": None,
            }
        sum_full = float(np.sum(full_residual[aperture]**2))
        sum_null = float(np.sum(null_residual[aperture]**2))
        # Since RSS(null)-RSS(full) = 2<e_null,d> - <d,d>,
        # allocate each cross-term symmetrically to its two components:
        # G_i = 2<e_null,d_i> - <d_i,d>. Their sum reproduces EXACTLY
        # the signed RSS gain, including a local worsening. This is
        # an algebraic partition, not a causal source-identification
        # or a significance test. The fitted sky/stellar amplitudes are
        # mutually coupled and depend on the entire expanded field.
        change = total_model_change[aperture]
        residual = null_residual[aperture]
        gains = {
            name: float(2*np.dot(residual, values[aperture])
                        -np.dot(values[aperture], change))
            for name, values in model_changes.items()
        }
        neighbors = model_changes["direct_catalog_point_neighbors"][aperture]
        return {
            "status": "SAME_NATIVE_PIXELS_WITH_GLOBAL_PARAMETERS",
            "n_native_pixels": int(np.count_nonzero(aperture)),
            "radius_pix": (float(radius_pix)
                           if radius_pix is not None else None),
            "weighted_squared_residual_full": sum_full,
            "weighted_squared_residual_star_plane_only": sum_null,
            "signed_improvement_fraction": (
                float((sum_null-sum_full)/sum_null) if sum_null > 0 else None),
            "not_a_covariance_calibrated_or_independent_validation": True,
            "retains_negative_improvement_when_central_fit_worsens": True,
            "weighted_rss_gain_partition_by_fitted_model_change": gains,
            "weighted_rss_gain_partition_closure_error": float(
                sum(gains.values())-(sum_null-sum_full)),
            "direct_catalog_point_neighbors_model_power_weighted":
                float(neighbors@neighbors),
            "gain_partition_is_algebraic_noncausal_with_symmetric_cross_terms": True,
            "background_and_central_amplitude_changes_are_globally_coupled": True,
            "no_new_local_refit_or_science_adoption": True,
        }
    def neighbor_fraction(aperture):
        if not np.any(aperture):
            return None
        central_flux = float(
            (amplitudes[0]/template_peak_scales[0])*
            np.sum(templates[0][aperture]))
        other_flux = float(sum(
            (amplitudes[i]/template_peak_scales[i])*
            np.sum(templates[i][aperture])
            for i in range(1, len(templates))))
        return (other_flux/central_flux if central_flux > 0 else None)

    result = {
        "schema": SCHEMA,
        "status": "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC",
        "scene_width_pix": width,
        "scene_geometry": "2*EMPIRICAL_KERNEL_WIDTH_MINUS_ONE",
        "scene_size_is_not_a_measured_wing_boundary": True,
        "n_original_native_pixels": width*width,
        "n_valid_native_pixels": n_valid,
        "valid_native_pixel_fraction": float(n_valid/(width*width)),
        "no_arbitrary_valid_pixel_fraction_cut_imposed": True,
        "n_catalog_point_neighbors": len(active_neighbors),
        "n_catalog_point_neighbors_in_expanded_search": len(neighbors),
        "n_catalog_neighbors_with_exact_zero_finite_psf_support":
            len(zero_support_neighbors),
        "catalog_neighbors_with_exact_zero_finite_psf_support":
            zero_support_neighbors,
        "zero_support_exclusion_is_only_relative_to_frozen_kernel": True,
        "neighbors": [{
            "tractor_objid": int(row["tractor_objid"]),
            "tractor_type": row["tractor_type"],
            "separation_from_star_pix": float(
                math.hypot(row["x_pix"]-cx, row["y_pix"]-cy)),
            "catalog_band_flux_nMgy": float(row["flux_nMgy"]),
            "fitted_peak_nMgy_per_pixel": float(amplitudes[i+1]),
            "fitted_flux_within_model_support_nMgy": float(
                amplitudes[i+1]/template_peak_scales[i+1] *
                np.sum(templates[i+1])),
        } for i, row in enumerate(active_neighbors)],
        "target_fitted_peak_nMgy_per_pixel": float(amplitudes[0]),
        "star_only_null_fitted_peak_nMgy_per_pixel":
            float(null_amplitudes[0]),
        "full_minus_null_fitted_peak_nMgy_per_pixel":
            float(amplitudes[0]-null_amplitudes[0]),
        "star_only_null_fitted_sky_at_star_nMgy_per_native_pixel":
            float(null_sky[0]),
        "full_minus_null_fitted_sky_at_star_nMgy_per_native_pixel":
            float(sky[0]-null_sky[0]),
        "target_fitted_model_support_flux_nMgy": float(
            amplitudes[0]/template_peak_scales[0] * np.sum(templates[0])),
        "fitted_sky_at_star_nMgy_per_native_pixel": float(sky[0]),
        "fitted_sky_gradient_x_nMgy_per_native_pixel_per_pix":
            float(sky[1]/extent),
        "fitted_sky_gradient_y_nMgy_per_native_pixel_per_pix":
            float(sky[2]/extent),
        "original_annulus_sky_at_star_nMgy_per_native_pixel":
            float(original_sky_nMgy_pix),
        "joint_minus_annulus_sky_nMgy_per_native_pixel":
            float(sky[0]-original_sky_nMgy_pix),
        "neighbor_to_central_core_model_flux_ratio":
            neighbor_fraction(within_core),
        "neighbor_to_central_4fwhm_model_flux_ratio":
            neighbor_fraction(within_outer),
        "weighted_squared_residual_star_plane_only": float(null_rss),
        "weighted_squared_residual_star_neighbors_plane": float(full_rss),
        "central_aperture_residual_comparison": {
            "entire_native_scene": radial_residual_comparison(None),
            "one_guessed_fwhm": radial_residual_comparison(
                float(guessed_fwhm_pix)),
            "four_guessed_fwhm": radial_residual_comparison(
                float(4*guessed_fwhm_pix)),
            "same_global_fit_coefficients_no_local_refit": True,
            "not_an_independent_psf_accuracy_test": True,
        },
        "weighted_residual_improvement_fraction": (
            float(max(0., null_rss-full_rss)/null_rss)
            if null_rss > 0 else None),
        "joint_source_matrix_condition_number": condition,
        "null_source_matrix_condition_number": null_condition,
        "joint_fit_uses_original_unsmoothed_unsubtracted_pixels": True,
        "star_and_neighbor_templates_fixed_to_existing_empirical_kernel": True,
        "point_source_fits_nonnegative": True,
        "sky_fit_unconstrained_planar": True,
        "kernel_support_beyond_observed_ePSF_unmodeled": True,
        "catalog_extended_neighbors_not_forward_modeled": True,
        "correlated_noise_and_fit_parameter_covariance_unmodeled": True,
        "comparison_fit_has_more_parameters_and_cannot_be_science_gate": True,
        "science_adopted": False,
        "changes_psf_or_source_selection_or_original_pixels": False,
    }
    return result


def audit_fixed_psf_joint_scenes(
    frame, kernel, stars, catalog_matcher, *,
    protected_mask, guessed_fwhm_pix,
):
    """Evaluate the SAME preselected stars. Never resplit or retune sample."""
    if catalog_matcher is None:
        return {
            "schema": SCHEMA, "status": "NO_NATIVE_TRACTOR_CATALOG",
            "n_star_rows": len(stars), "science_adopted": False,
        }
    if catalog_matcher.dataset_id != frame.dataset_id:
        raise ValueError("Joint scene catalog dataset does not match native FITS")
    kernel_arr = np.asarray(kernel, dtype=float)
    half = kernel_arr.shape[0]-1  # half-width of (2*N - 1) patch
    kh = kernel_arr.shape[0]//2
    max_extent = half+kh+1
    index = cKDTree(catalog_matcher.pixel_positions)
    if protected_mask.shape != frame.shape:
        raise ValueError("Protected target mask differs from native coadd")
    # Compute the original native acceptance mask ONCE per analysis band;
    # avoid copying a full 3600x3600 frame separately for every star.
    valid = np.asarray(frame.valid, dtype=bool)
    for key in (f"SATUR_{frame.band.upper()}",
                f"ALLMASK_{frame.band.upper()}"):
        if key in frame.mask_reasons:
            valid = valid & ~frame.mask_reasons[key]
    valid = valid & ~protected_mask
    outputs = []
    for star in stars:
        x, y = float(star.x_pix), float(star.y_pix)
        item = {"source_id": str(star.star_id), "role": str(star.role),
                "gaia_source_id": star.gaia_source_id}
        matches = index.query_ball_point((x, y),
                                         r=float(catalog_matcher.match_radius_pix))
        if len(matches) != 1:
            item["status"] = "CENTRAL_TRACTOR_MATCH_NOT_UNIQUE"
            item["n_central_matches"] = len(matches)
            outputs.append(item)
            continue
        own = int(matches[0])
        own_row = catalog_matcher.rows[own]
        if (star.gaia_source_id is not None and
                int(own_row.get("ref_id", -1)) != star.gaia_source_id):
            item["status"] = "CENTRAL_GAIA_ID_MISMATCH"
            outputs.append(item)
            continue
        if own_row.get("type") != "PSF":
            item["status"] = "CENTRAL_TRACTOR_SOURCE_NOT_PSF"
            outputs.append(item)
            continue
        neighboring = []
        extended = []
        # Query source positions over the patch+full ePSF support, NOT
        # only within the existing 50px significant-neighbor preflight.
        candidates = index.query_ball_point((x,y), r=math.sqrt(2)*max_extent)
        for j in candidates:
            if j == own:
                continue
            px, py = map(float, catalog_matcher.pixel_positions[j])
            if abs(px-x) > max_extent or abs(py-y) > max_extent:
                continue
            row = catalog_matcher.rows[j]
            flux = _finite_float(row.get("flux"))
            record = {
                "x_pix":px, "y_pix":py,
                "tractor_objid": int(row["objid"]),
                "tractor_type": row["type"],
                "flux_nMgy": flux,
                # These MUST originate from the original DR11 Tractor
                # table; missing morphology remains None, never guessed.
                "shape_r_arcsec": row.get("shape_r_arcsec"),
                "shape_e1": row.get("shape_e1"),
                "shape_e2": row.get("shape_e2"),
                "sersic_n": row.get("sersic_n"),
                "shape_r_ivar_arcsec_m2":
                    row.get("shape_r_ivar_arcsec_m2"),
                "shape_e1_ivar": row.get("shape_e1_ivar"),
                "shape_e2_ivar": row.get("shape_e2_ivar"),
            }
            if row["type"] == "PSF" and flux is not None and flux > 0:
                neighboring.append(record)
            else:
                extended.append(record)
        neighboring.sort(key=lambda r: (
            math.hypot(r["x_pix"]-x,r["y_pix"]-y),r["tractor_objid"]))
        extended.sort(key=lambda r: (
            math.hypot(r["x_pix"]-x,r["y_pix"]-y),r["tractor_objid"]))
        item["n_unmodeled_extended_or_nonpositive_catalog_sources"] = len(extended)
        item["nearest_unmodeled_source_distance_pix"] = (
            float(math.hypot(extended[0]["x_pix"]-x,
                             extended[0]["y_pix"]-y))
            if extended else None)
        item["nearest_unmodeled_catalog_source"] = ({
            "tractor_objid": extended[0]["tractor_objid"],
            "tractor_type": extended[0]["tractor_type"],
            "catalog_band_flux_nMgy": extended[0]["flux_nMgy"],
            "separation_from_star_pix":
                item["nearest_unmodeled_source_distance_pix"],
            "reason": (
                "TRACTOR_NON_PSF_MODEL" if
                extended[0]["tractor_type"] != "PSF"
                else "PSF_NONPOSITIVE_OR_INVALID_CATALOG_FLUX"),
            "source_not_forward_modeled": True,
        } if extended else None)
        item["catalog_point_neighbor_count_full_support"] = len(neighboring)
        item["catalog_scene_fully_modelled"] = len(extended) == 0
        item["known_unmodeled_catalog_sources_prevent_science_certification"] = (
            len(extended) > 0)
        item["nearest_catalog_point_neighbor_distance_pix"] = (
            float(math.hypot(neighboring[0]["x_pix"]-x,
                             neighboring[0]["y_pix"]-y))
            if neighboring else None)
        fitted = fit_fixed_psf_scene(
            frame.image_nMgy, frame.invvar_nMgy2, valid,
            kernel=kernel_arr, target_xy=(x,y),
            neighbors=neighboring,
            guessed_fwhm_pix=guessed_fwhm_pix,
            original_sky_nMgy_pix=star.cutout_sky_nMgy)
        item.update(fitted)
        # P15: one separate, non-adopting observational comparison of
        # actual DR11 REX/EXP/DEV morphology, never fed back into PSF.
        # A local import avoids a cycle: the extended renderer reuses
        # the already tested projected-NNLS primitives in this module.
        if fitted["status"] != "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC":
            item["p15_extended_tractor_scene"] = {
                "status":"NOT_RUN_NO_IDENTIFIABLE_P14_POINT_SCENE",
                "science_adopted":False,
            }
        elif getattr(frame,"wcs",None) is None:
            item["p15_extended_tractor_scene"] = {
                "status":"NOT_RUN_NO_VERIFIED_ORIGINAL_WCS",
                "science_adopted":False,
            }
        else:
            from lsbpipe.photometry.psf_extended_scene import (
                pilot_extended_scene,
            )
            item["p15_extended_tractor_scene"] = pilot_extended_scene(
                frame.image_nMgy, frame.invvar_nMgy2, valid,
                wcs=frame.wcs, kernel=kernel_arr,
                target_xy=(x,y), point_sources=neighboring,
                extended_sources=extended,
                guessed_fwhm_pix=guessed_fwhm_pix)
        outputs.append(item)
    ok = [row for row in outputs
          if row["status"] == "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"]
    return {
        "schema": SCHEMA,
        "status": "FIXED_PSF_ORIGINAL_NATIVE_SCENE_AUDIT_NO_SCIENCE_ADOPTION",
        "dataset_id": frame.dataset_id,
        "native_tractor_catalog_sha256": catalog_matcher.source_sha256,
        "frozen_input_kernel_sha256":
            sha256(kernel_arr.tobytes(order="C")).hexdigest(),
        "n_preselected_stars": len(stars),
        "n_completed_joint_scene_fits": len(ok),
        "n_failed_joint_scene_fits": len(stars)-len(ok),
        "n_completed_scenes_with_unmodeled_catalog_sources": sum(
            row.get("known_unmodeled_catalog_sources_prevent_science_certification",
                    False)
            for row in ok),
        "n_completed_scenes_with_zero_support_catalog_psf_neighbors": sum(
            row.get("n_catalog_neighbors_with_exact_zero_finite_psf_support",
                    0) > 0 for row in ok),
        "n_total_zero_support_catalog_psf_neighbors_in_completed_fits": sum(
            row.get("n_catalog_neighbors_with_exact_zero_finite_psf_support",
                    0) for row in ok),
        "n_completed_fixed_holdouts": sum(
            row["role"] == "holdout" for row in ok),
        "n_holdouts_expected": sum(star.role == "holdout" for star in stars),
        "per_star": outputs,
        "p15_extended_scene_pilot_summary": {
            "schema": "desidera.psf0-native-tractor-extended-scene-summary/1",
            "n_original_stars":len(stars),
            "n_attempted_after_p14":sum(
                row.get("p15_extended_tractor_scene", {}).get("status")
                not in ("NOT_RUN_NO_IDENTIFIABLE_P14_POINT_SCENE",
                        "NOT_RUN_NO_VERIFIED_ORIGINAL_WCS")
                for row in outputs),
            "n_numerical_partial_extended_fits":sum(
                row.get("p15_extended_tractor_scene", {}).get("status")
                == "PARTIAL_APPROXIMATE_EXTENDED_SCENE_DIAGNOSTIC"
                for row in outputs),
            "n_fits_with_unmodelled_extended_sources":sum(
                row.get("p15_extended_tractor_scene", {}).get(
                    "n_extended_catalog_unmodeled",0)>0 for row in outputs),
            "no_psf_adoption_or_star_split_changes":True,
            "not_exact_tractor_profile_or_noise_covariance":True,
            "science_adopted":False,
        },
        "all_stars_audited_without_resplitting": True,
        "catalog_only_nuisance_sources_are_not_new_training_stars": True,
        "frozen_psf_still_has_no_wide_angle_wings": True,
        "unmodeled_extended_sources_not_certified_clean": True,
        "coadd_covariance_not_calibrated": True,
        "nuisance_parameter_fit_to_holdouts_not_blind_validation": True,
        "not_used_to_select_psf_recipe_or_science_thresholds": True,
        "science_adopted": False,
    }
