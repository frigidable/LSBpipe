"""P15 exploratory native-pixel PSF-convolved Tractor galaxy scene model.

Fixed original coadd pixels and already-fitted experimental PSF. REX,
EXP and DEV shapes use actual same-brick Tractor catalog metadata,
not fabricated radii. The analytic oversampled Sersic + FFT model is
NOT exactly the Tractor Gaussian-mixture, pixelization or PSF renderer.
Never use these diagnostics for PSF adoption or calibrated photometry.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import fftconvolve

from lsbpipe.photometry.psf_joint_scene import (
    _projected_nnls, _shifted_native_kernel,
)

SCHEMA = "desidera.psf0-native-tractor-extended-scene/2"
SERSIC_B = {1.: 1.6783469900166612, 4.: 7.669249442500804}
PROFILE_N = {"REX": 1., "EXP": 1., "DEV": 4.}
SUBPIXEL_GRID = 3
MAX_NUISANCE_SOURCES = 28  # compute cap: fail whole fit, never truncate


def finite(value):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def audit_tractor_shape(row):
    kind = str(row.get("tractor_type", row.get("type", ""))).strip().upper()
    radius = finite(row.get("shape_r_arcsec"))
    e1, e2 = finite(row.get("shape_e1")), finite(row.get("shape_e2"))
    out = {
        "type": kind, "shape_r_arcsec": radius,
        "shape_e1": e1, "shape_e2": e2,
        "catalog_sersic_n": finite(row.get("sersic_n")),
        "flux_not_used_as_amplitude_prior": True,
        "science_adopted": False,
    }
    if kind not in PROFILE_N:
        out["status"] = "UNSUPPORTED_TRACTOR_PROFILE_TYPE"
        return out
    if radius is None or radius <= 0:
        out["status"] = "MISSING_OR_INVALID_HALF_LIGHT_RADIUS_ARCSEC"
        return out
    if kind == "REX":
        # REX is round exponential by catalog model definition.
        q, pa = 1., None
    else:
        if e1 is None or e2 is None:
            out["status"] = "MISSING_TRACTOR_ELLIPTICITY"
            return out
        e = math.hypot(e1, e2)
        if e >= 1.:
            out["status"] = "INVALID_TRACTOR_ELLIPTICITY"
            return out
        q = (1.-e)/(1.+e)
        # DR11 convention PA = (180 - phi) % 180,
        # phi = 0.5 atan2(e2,e1). PA from North to East.
        pa = (180.-.5*math.degrees(math.atan2(e2,e1)))%180.
    out.update({
        "status": "VALID_APPROXIMATE_EXTENDED_TEMPLATE",
        "sersic_n": PROFILE_N[kind], "axis_ratio": q,
        "position_angle_east_of_north_deg": pa,
        "rex_round_by_definition": kind == "REX",
        "not_exact_tractor_renderer": True,
    })
    return out


def local_tangent_jacobian(wcs, xy):
    """Arcsec east/north per native image x/y pixel, including sky rotation."""
    x, y = map(float, xy)
    ra, dec = wcs.all_pix2world(
        np.array([x,x+1.,x]), np.array([y,y,y+1.]), 0)
    ra, dec = np.asarray(ra,float), np.asarray(dec,float)
    if not np.isfinite(ra).all() or not np.isfinite(dec).all():
        raise ValueError("NONFINITE_WCS_PROJECTION")
    dr = ((ra[1:]-ra[0]+180.)%360.-180.)
    east = dr*np.cos(np.deg2rad(dec[0]))*3600.
    north = (dec[1:]-dec[0])*3600.
    J = np.array([[east[0],east[1]],[north[0],north[1]]])
    if (not np.isfinite(J).all()
            or abs(np.linalg.det(J))<1e-9
            or np.linalg.cond(J)>1e4):
        raise ValueError("DEGENERATE_LOCAL_WCS")
    return J


def convolved_native_sersic(*, kernel, wcs, source_xy,
                             origin_xy, width, morphology,
                             subpixel_grid=SUBPIXEL_GRID):
    """Finite-domain, subpixel integrated intrinsic Sersic convolved with PSF.

    Total-flux normalization is intentionally NOT claimed: intrinsic
    DEV wings beyond the finite modeled domain are unmeasured.
    """
    if morphology.get("status") != "VALID_APPROXIMATE_EXTENDED_TEMPLATE":
        raise ValueError("NO_VALID_NATIVE_TRACTOR_MORPHOLOGY")
    # Only controlled odd grids are permitted. Existing P15 work
    # continues to use the unchanged default 3x3 reference geometry.
    if type(subpixel_grid) is not int or subpixel_grid not in (3,5,7):
        raise ValueError("UNSUPPORTED_DECLARED_SUBPIXEL_GRID")
    k = np.asarray(kernel,float)
    if (k.ndim!=2 or k.shape[0]!=k.shape[1] or
            k.shape[0]%2!=1 or type(width) is not int or width<=0
            or not np.isfinite(k).all() or np.min(k)<0
            or not np.isclose(k.sum(),1.,rtol=1e-8,atol=1e-8)):
        raise ValueError("INVALID_FROZEN_NATIVE_PSF")
    n = morphology["sersic_n"]
    q = morphology["axis_ratio"]
    r_eff = morphology["shape_r_arcsec"]
    pa = morphology["position_angle_east_of_north_deg"]
    if n not in SERSIC_B or not 0<q<=1:
        raise ValueError("INVALID_SERSIC_GEOMETRY")
    theta = math.radians(0. if pa is None else pa)
    J = local_tangent_jacobian(wcs,source_xy)
    pad = k.shape[0]//2
    yy,xx = np.indices((width+2*pad,width+2*pad),dtype=float)
    xx += float(origin_xy[0])-pad
    yy += float(origin_xy[1])-pad
    intrinsic = np.zeros_like(xx)
    for iy in range(subpixel_grid):
        for ix in range(subpixel_grid):
            dx = xx+(ix+.5)/subpixel_grid-.5-float(source_xy[0])
            dy = yy+(iy+.5)/subpixel_grid-.5-float(source_xy[1])
            east=J[0,0]*dx+J[0,1]*dy
            north=J[1,0]*dx+J[1,1]*dy
            major=east*math.sin(theta)+north*math.cos(theta)
            minor=east*math.cos(theta)-north*math.sin(theta)
            elliptical_R=np.hypot(major,minor/q)
            intrinsic += np.exp(-SERSIC_B[n]*
                                (elliptical_R/r_eff)**(1./n))
    intrinsic /= subpixel_grid**2
    if not np.isfinite(intrinsic).all() or intrinsic.max()<=0.:
        raise ValueError("NONFINITE_INTRINSIC_PROFILE")
    img=fftconvolve(intrinsic,k,mode="same")
    out=np.maximum(img[pad:pad+width,pad:pad+width],0.)
    if not np.isfinite(out).all():
        raise ValueError("NONFINITE_CONVOLVED_PROFILE")
    return out




def _shape_difference_on_same_native_pixels(reference, candidate, mask):
    """Unit-flux patch shape differences, not a photometric error model."""
    ref = np.asarray(reference, dtype=float)
    cand = np.asarray(candidate, dtype=float)
    valid = np.asarray(mask, dtype=bool)
    if (ref.shape != cand.shape or ref.shape != valid.shape
            or ref.ndim != 2 or not valid.any()
            or not np.isfinite(ref[valid]).all()
            or not np.isfinite(cand[valid]).all()
            or np.any(ref[valid] < 0)
            or np.any(cand[valid] < 0)):
        raise ValueError("INCOMPARABLE_NATIVE_REFERENCE_STAMPS")
    fr = float(np.sum(ref[valid]))
    fc = float(np.sum(cand[valid]))
    if fr <= 0 or fc <= 0:
        raise ValueError("NONPOSITIVE_NATIVE_REFERENCE_STAMP_FLUX")
    r = ref[valid]/fr
    c = cand[valid]/fc
    diff = c-r
    return {
        "relative_finite_patch_flux_difference": float((fc-fr)/fr),
        "shape_l1_unit_flux_difference": float(np.sum(np.abs(diff))),
        "shape_linf_unit_flux_difference": float(np.max(np.abs(diff))),
        "l2_unit_flux_difference": float(np.sqrt(diff@diff)),
        "relative_patch_normalization_not_source_total_flux": True,
        "shape_errors_are_unweighted_geometry_only": True,
    }


def audit_approximate_sersic_sampling(*, kernel, wcs, source_xy,
                                      origin_xy, width, morphology, valid,
                                      baseline_template=None):
    """3/5/7 native-intrinsic quadrature diagnostic, NOT Tractor validation.

    For each grid, keep the SAME already-pixel-convolved frozen ePSF,
    catalog geometry and finite padded domain. Tests numerical
    quadrature convergence within this *one* analytic renderer only.
    """
    mask = np.asarray(valid, dtype=bool)
    out = {
        "schema": "desidera.psf0-p15e-native-sersic-subpixel-convergence/1",
        "status": "UNINITIALIZED_SUBPIXEL_CONVERGENCE",
        "evaluated_intrinsic_subpixel_grids": [3, 5, 7],
        "single_frozen_native_empirical_psf_all_grids": True,
        "same_catalog_morphology_and_native_wcs_all_grids": True,
        "same_scene_geometry_and_original_valid_pixel_mask": True,
        "not_a_comparison_against_tractor_mixture_of_gaussians": True,
        "psf_already_pixel_integrated": True,
        "finite_intrinsic_profile_and_psf_support_unvalidated": True,
        "numerical_convergence_not_a_measurement_uncertainty": True,
        "no_automatic_threshold_or_best_grid_selection": True,
        "science_adopted": False,
    }
    if mask.shape != (width, width) or not mask.any():
        return dict(out, status="INVALID_OR_EMPTY_NATIVE_REFERENCE_MASK")
    try:
        images = {}
        for grid in (3,5,7):
            if grid == 3 and baseline_template is not None:
                img = np.asarray(baseline_template, dtype=float)
                if img.shape != (width, width):
                    raise ValueError("INVALID_BASELINE_RENDERED_STAMP_SHAPE")
                images[grid] = img
            else:
                images[grid] = convolved_native_sersic(
                    kernel=kernel, wcs=wcs, source_xy=source_xy,
                    origin_xy=origin_xy, width=width,
                    morphology=morphology, subpixel_grid=grid)
        a35 = _shape_difference_on_same_native_pixels(
            images[3], images[5], mask)
        a57 = _shape_difference_on_same_native_pixels(
            images[5], images[7], mask)
        a37 = _shape_difference_on_same_native_pixels(
            images[3], images[7], mask)
    except (ValueError, ArithmeticError, FloatingPointError) as exc:
        return dict(out, status="SERSIC_SUBPIXEL_CHECK_NOT_NUMERICALLY_VALID",
                    reason=str(exc))
    return dict(
        out, status="THREE_GRID_ANALYTIC_CONVERGENCE_MEASURED_NOT_TRACTOR",
        n_original_valid_scene_pixels=int(mask.sum()),
        three_to_five=a35,
        five_to_seven=a57,
        three_to_seven=a37,
        five_to_seven_not_proven_zero_or_physical_reference=True,
    )


def compare_analytic_stamp_to_external_tractor(
    analytic_stamp, reference_stamp, valid, *, provenance,
):
    """Compare only a supplied external reference, never fabricate one.

    The caller must provide a pre-rendered identical-coordinate,
    identical-PSF Tractor source stamp. Its metadata is descriptive
    self-reported evidence, not proof of engine identity or that the
    survey exposure PSF equals the coadd ePSF. No Tractor GPL code
    is incorporated or installed by Desidera.
    """
    required = (
        "renderer", "renderer_version_or_commit", "tractor_source_type",
        "source_identifier", "native_wcs_fingerprint",
        "input_psf_fingerprint", "reference_generation_recipe",
    )
    out = {
        "schema": "desidera.psf0-p15e-external-tractor-stamp-comparison/1",
        "status": "UNINITIALIZED_EXTERNAL_REFERENCE_CHECK",
        "require_original_same_source_same_wcs_same_psf": True,
        "external_reference_identity_self_reported_not_verified": True,
        "comparison_does_not_certify_tractor_reproduction": True,
        "no_gpl_tractor_code_imported_or_reproduced": True,
        "science_adopted": False,
    }
    if not isinstance(provenance, dict):
        return dict(out, status="NO_EXTERNAL_REFERENCE_PROVENANCE")
    missing = [
        key for key in required
        if not isinstance(provenance.get(key),str)
        or not provenance[key].strip()
    ]
    if missing:
        return dict(out, status="INCOMPLETE_EXTERNAL_REFERENCE_PROVENANCE",
                    missing_fields=missing)
    if provenance["renderer"] != "dstndstn/tractor":
        return dict(out, status="NOT_AN_IDENTIFIED_TRACTOR_RENDERER")
    try:
        diff = _shape_difference_on_same_native_pixels(
            reference_stamp, analytic_stamp, valid)
    except (ValueError, ArithmeticError) as exc:
        return dict(out, status="INCOMPARABLE_EXTERNAL_REFERENCE_ARRAYS",
                    reason=str(exc))
    return dict(
        out,
        status="EXTERNAL_REFERENCE_ARRAYS_COMPARED_NOT_SCIENCE_CERTIFIED",
        supplied_external_reference_provenance=provenance,
        common_native_pixel_shape_comparison=diff,
        still_requires_independent_psf_and_wcs_lineage_verification=True,
    )

def _predeclared_ring_prediction(z, B, P, weights, radius_pix, *,
                                guessed_fwhm_pix, n_base_columns):
    """Withhold 2–4 detection FWHM annulus from BOTH model fits.

    Fixed, predeclared annular split on ORIGINAL same-pixel coadds.
    Templates/morphology/PSF are frozen before the split. This is
    predictive *pixel* QA only, NOT an independent star validation:
    resampled coadd pixel covariances cross the train/test boundary.
    """
    ring = ((radius_pix > 2.*guessed_fwhm_pix)
            & (radius_pix <= 4.*guessed_fwhm_pix))
    train = ~ring
    out = {
        "schema": "desidera.psf0-p15-native-annular-predictive-check/1",
        "heldout_radius_inner_guessed_fwhm": 2.,
        "heldout_radius_outer_guessed_fwhm": 4.,
        "n_fitting_native_pixels": int(np.count_nonzero(train)),
        "n_reserved_native_pixels": int(np.count_nonzero(ring)),
        "both_fits_exclude_all_reserved_ring_pixels": True,
        "fixed_templates_morphology_and_psf_prior_to_pixel_split": True,
        "predictive_test_uses_same_original_native_pixel_mask": True,
        "pixel_resampling_covariance_across_ring_boundary_unmodeled": True,
        "psf_itself_not_independently_trained_for_every_star": True,
        "no_model_selection_or_science_adoption": True,
    }
    if (not np.any(ring) or
            np.count_nonzero(train) <= P.shape[1]+3):
        return dict(out, status="INSUFFICIENT_RING_OR_TRAINING_PIXELS")
    try:
        fitted, sky, _, condition = _projected_nnls(
            z[train], B[train], P[train], weights[train])
        baseline, sky_base, _, condition_base = _projected_nnls(
            z[train], B[train], P[train, :n_base_columns],
            weights[train])
    except (ValueError, RuntimeError) as exc:
        return dict(
            out, status="RING_EXCLUDED_FITS_NOT_IDENTIFIABLE",
            reason=str(exc))
    prediction = (
        z[ring] - B[ring]@sky - P[ring]@fitted)
    baseline_prediction = (
        z[ring] - B[ring]@sky_base
        - P[ring,:n_base_columns]@baseline)
    score = float(np.dot(
        weights[ring]*prediction, weights[ring]*prediction))
    score_base = float(np.dot(
        weights[ring]*baseline_prediction,
        weights[ring]*baseline_prediction))
    return dict(
        out, status="PREDECLARED_RING_OUT_OF_SAMPLE_PIXEL_PREDICTION",
        weighted_reserved_ring_rss_with_extended=score,
        weighted_reserved_ring_rss_without_extended=score_base,
        signed_predictive_ring_rss_improvement_fraction=(
            float((score_base-score)/score_base)
            if score_base>0 else None),
        training_extended_design_condition_number=float(condition),
        training_point_only_design_condition_number=float(condition_base),
        negative_ring_gain_retained_instead_of_clamped=True,
        not_chi_squared_without_coadd_covariance=True,
    )


# Fixed exploratory perturbation magnitudes, not measured Tractor errors.
# Changing them requires a new method fingerprint; none is calibrated.
P15D_RADIUS_FRACTION = 0.10
P15D_ELLIPTICITY_DELTA = 0.05
P15D_POSITION_ANGLE_DEG = 10.


def morphology_stress_variants(row):
    """Six predeclared one-at-a-time morphology cases, including refusals.

    Preserve the Tractor (e1,e2) doubled-angle convention. For REX,
    ellipticity and PA are not degrees of freedom. For a round EXP/DEV,
    PA is undefined: never invent a reference orientation. If a
    perturbation leaves the physical e<1 domain, refuse instead of clip.
    """
    nominal = audit_tractor_shape(row)
    if nominal["status"] != "VALID_APPROXIMATE_EXTENDED_TEMPLATE":
        return []
    variants = []
    for direction in (-1, +1):
        label = "minus" if direction < 0 else "plus"
        changed = dict(row)
        changed["shape_r_arcsec"] = (
            float(row["shape_r_arcsec"]) *
            (1. + direction * P15D_RADIUS_FRACTION))
        variants.append({
            "parameter": "half_light_radius",
            "case": f"half_light_radius_{label}_10_percent",
            "signed_controlled_change": (
                direction * P15D_RADIUS_FRACTION),
            "change_units": "fraction_of_catalog_radius",
            "status": "VALID_CONTROLLED_VARIANT",
            "catalog_row": changed,
        })
    kind = nominal["type"]
    e1 = nominal["shape_e1"]
    e2 = nominal["shape_e2"]
    for direction in (-1,+1):
        label = "minus" if direction < 0 else "plus"
        record = {
            "parameter": "ellipticity_norm",
            "case": f"ellipticity_norm_{label}_0p05",
            "signed_controlled_change":
                direction * P15D_ELLIPTICITY_DELTA,
            "change_units": "absolute_euclidean_ellipticity_norm",
        }
        if kind == "REX":
            record["status"] = "NOT_A_REX_PROFILE_PARAMETER"
        else:
            e = math.hypot(e1,e2)
            altered = e + direction * P15D_ELLIPTICITY_DELTA
            if e == 0.:
                record["status"] = "UNDEFINED_CIRCULAR_REFERENCE_PA"
            elif not 0. <= altered < 1.:
                record["status"] = "OUTSIDE_PHYSICAL_ELLIPTICITY_DOMAIN"
            else:
                changed = dict(row)
                changed["shape_e1"] = e1 * altered/e
                changed["shape_e2"] = e2 * altered/e
                record.update(
                    status="VALID_CONTROLLED_VARIANT",
                    catalog_row=changed,
                )
        variants.append(record)
    for direction in (-1,+1):
        label = "minus" if direction < 0 else "plus"
        record = {
            "parameter": "position_angle",
            "case": f"position_angle_{label}_10_deg",
            "signed_controlled_change":
                direction * P15D_POSITION_ANGLE_DEG,
            "change_units": "degrees_east_of_north",
        }
        if kind == "REX":
            record["status"] = "NOT_A_REX_PROFILE_PARAMETER"
        elif math.hypot(e1,e2) == 0.:
            record["status"] = "UNDEFINED_CIRCULAR_REFERENCE_PA"
        else:
            # PA = 180 - 0.5 atan2(e2,e1); rotating PA by +delta
            # rotates the (e1,e2) complex vector by -2*delta.
            theta = math.radians(
                2. * direction * P15D_POSITION_ANGLE_DEG)
            c, t = math.cos(theta), math.sin(theta)
            changed = dict(row)
            changed["shape_e1"] = e1*c + e2*t
            changed["shape_e2"] = -e1*t + e2*c
            record.update(
                status="VALID_CONTROLLED_VARIANT",
                catalog_row=changed,
            )
        variants.append(record)
    return variants


def _morphology_stress_p15d(
    *, z, B, P, weights, native_radius, mask, kernel, wcs,
    scene_origin_xy, scene_width_pix, guessed_fwhm_pix,
    source_row, template_column, n_rendered_extended_sources,
):
    """Rerender ONE geometrically preselected catalog source per star.

    No shape variant is selected by its residual. Freeze the native
    pixels, PSF, other catalog templates, candidate identity, and 2–4
    guessed-FWHM reserved ring. Fit *outside* the ring for each case.
    Returned deltas are conditional model sensitivity, NOT source
    uncertainties, posteriors, calibrated error bars, or model ranking.
    """
    heldout = ((native_radius > 2.*guessed_fwhm_pix)
               & (native_radius <= 4.*guessed_fwhm_pix))
    train = ~heldout
    result = {
        "schema": "desidera.psf0-p15d-tractor-morphology-stress/1",
        "status": "UNINITIALIZED_MORPHOLOGY_STRESS",
        "selected_tractor_objid": int(source_row["tractor_objid"]),
        "selected_tractor_type": str(source_row["tractor_type"]),
        "nominal_catalog_formal_shape_ivars_not_covariance_or_stress_scale": {
            "shape_r_ivar_arcsec_m2":
                finite(source_row.get("shape_r_ivar_arcsec_m2")),
            "shape_e1_ivar": finite(source_row.get("shape_e1_ivar")),
            "shape_e2_ivar": finite(source_row.get("shape_e2_ivar")),
        },
        "source_selection_policy":
            "NEAREST_RENDERABLE_EXTENDED_CENTROID_PRIOR_TO_FITTING",
        "n_rendered_extended_sources_in_original_fit":
            int(n_rendered_extended_sources),
        "only_one_extended_source_perturbed_at_a_time": True,
        "all_other_rendered_catalog_components_frozen": True,
        "radius_fractional_stress": P15D_RADIUS_FRACTION,
        "ellipticity_norm_absolute_stress": P15D_ELLIPTICITY_DELTA,
        "position_angle_degree_stress": P15D_POSITION_ANGLE_DEG,
        "perturbations_are_not_catalog_sigma_or_uncertainty": True,
        "n_fitting_original_native_pixels": int(np.count_nonzero(train)),
        "n_reserved_original_native_pixels": int(np.count_nonzero(heldout)),
        "reserved_2_to_4_guessed_fwhm_never_used_in_fits": True,
        "coadd_covariance_and_psf_uncertainty_unmodeled": True,
        "fitted_star_amplitude_and_sky_are_model_conditional": True,
        "stress_envelope_is_not_a_confidence_interval": True,
        "science_adopted": False,
    }
    if (not np.any(heldout)
            or np.count_nonzero(train) <= P.shape[1]+3):
        return dict(result, status="NO_IDENTIFIABLE_WITHHELD_RING_SPLIT")
    try:
        nominal_a, nominal_sky, _, _ = _projected_nnls(
            z[train], B[train], P[train], weights[train])
    except (ValueError, RuntimeError) as exc:
        return dict(
            result, status="NOMINAL_RING_EXCLUDED_FIT_UNIDENTIFIABLE",
            reason=str(exc))
    if nominal_a[0] <= 0:
        return dict(result, status="NOMINAL_STAR_AMPLITUDE_NONPOSITIVE")
    nominal_residual = (z[heldout]
                        - B[heldout]@nominal_sky
                        - P[heldout]@nominal_a) * weights[heldout]
    nominal_rss = float(nominal_residual@nominal_residual)
    result.update(
        nominal_ring_rss=nominal_rss,
        nominal_sky_at_star_nMgy_per_native_pixel=float(nominal_sky[0]),
        nominal_star_fitted_peak_nMgy_per_pixel=float(nominal_a[0]),
        nominal_ring_rss_may_be_near_zero_no_calibrated_floor=True,
    )
    cases = []
    for variant in morphology_stress_variants(source_row):
        entry = {
            k: v for k, v in variant.items() if k != "catalog_row"
        }
        if entry["status"] != "VALID_CONTROLLED_VARIANT":
            cases.append(entry)
            continue
        changed = variant["catalog_row"]
        morphology = audit_tractor_shape(changed)
        if morphology["status"] != "VALID_APPROXIMATE_EXTENDED_TEMPLATE":
            entry["status"] = "PERTURBED_MORPHOLOGY_INVALID"
            cases.append(entry)
            continue
        try:
            rendered = convolved_native_sersic(
                kernel=kernel, wcs=wcs,
                source_xy=(float(changed["x_pix"]),
                           float(changed["y_pix"])),
                origin_xy=scene_origin_xy,
                width=scene_width_pix, morphology=morphology)
        except (ValueError, ArithmeticError) as exc:
            entry["status"] = "PERTURBED_PROFILE_RENDER_FAILED"
            entry["reason"] = str(exc)
            cases.append(entry)
            continue
        pixels = rendered[mask]
        peak = float(np.max(pixels)) if len(pixels) else 0.
        if not math.isfinite(peak) or peak <= 0.:
            entry["status"] = "PERTURBED_TEMPLATE_WITHOUT_VALID_SUPPORT"
            cases.append(entry)
            continue
        design = P.copy()
        design[:, template_column] = pixels / peak
        try:
            fitted_a, fitted_sky, _, condition = _projected_nnls(
                z[train], B[train], design[train], weights[train])
        except (ValueError, RuntimeError) as exc:
            entry["status"] = "PERTURBED_RING_EXCLUDED_FIT_UNIDENTIFIABLE"
            entry["reason"] = str(exc)
            cases.append(entry)
            continue
        predicted = (z[heldout]
                     - B[heldout]@fitted_sky
                     - design[heldout]@fitted_a) * weights[heldout]
        rss = float(predicted@predicted)
        entry.update({
            "status": "CONTROLLED_PERTURBATION_FIT_SUCCESS",
            "resulting_shape_r_arcsec": float(changed["shape_r_arcsec"]),
            "resulting_axis_ratio":
                float(morphology["axis_ratio"]),
            "resulting_pa_east_of_north_deg":
                morphology["position_angle_east_of_north_deg"],
            "delta_sky_at_star_nMgy_per_native_pixel":
                float(fitted_sky[0]-nominal_sky[0]),
            "delta_star_fitted_peak_nMgy_per_pixel":
                float(fitted_a[0]-nominal_a[0]),
            "relative_delta_star_fitted_peak":
                float((fitted_a[0]-nominal_a[0])/nominal_a[0]),
            "reserved_ring_weighted_rss": rss,
            "delta_reserved_ring_weighted_rss":
                float(rss-nominal_rss),
            "signed_reserved_ring_gain_vs_catalog_shape": (
                float((nominal_rss-rss)/nominal_rss)
                if nominal_rss > 0 else None),
            "condition_number": float(condition),
            "ring_score_is_descriptive_not_noise_covariance_likelihood": True,
        })
        cases.append(entry)
    successful = [
        x for x in cases
        if x["status"] == "CONTROLLED_PERTURBATION_FIT_SUCCESS"
    ]
    result["cases"] = cases
    result["n_successful_stress_cases"] = len(successful)
    result["n_skipped_or_failed_cases"] = len(cases)-len(successful)
    if not successful:
        return dict(result, status="NO_VALID_MORPHOLOGY_STRESS_FITS")
    result["status"] = "PREDECLARED_MORPHOLOGY_STRESS_DIAGNOSTIC"
    result["descriptive_min_max_stress_response_not_error_bars"] = {
        name: {
            "min": float(min(case[name] for case in successful)),
            "max": float(max(case[name] for case in successful)),
        }
        for name in (
            "delta_sky_at_star_nMgy_per_native_pixel",
            "delta_star_fitted_peak_nMgy_per_pixel",
            "delta_reserved_ring_weighted_rss",
        )
    }
    return result

def pilot_extended_scene(image, invvar, valid, *, wcs, kernel, target_xy,
                         point_sources, extended_sources,
                         guessed_fwhm_pix, scene_width=None):
    """Fit same-pixel star+PSF-points+sky versus adding typed galaxies."""
    k=np.asarray(kernel,dtype=float)
    width=2*k.shape[0]-1 if scene_width is None else scene_width
    if type(width) is not int or width<k.shape[0] or width%2!=1:
        raise ValueError("INVALID_SCENE_WIDTH")
    image, invvar, valid = (np.asarray(image,float),
                              np.asarray(invvar,float),
                              np.asarray(valid,bool))
    if image.ndim!=2 or image.shape!=invvar.shape or image.shape!=valid.shape:
        raise ValueError("INCONSISTENT_ORIGINAL_IMAGE_ARRAYS")
    cx,cy=map(float,target_xy)
    half=width//2
    x0,y0=int(np.rint(cx))-half,int(np.rint(cy))-half
    base={"schema":SCHEMA,"science_adopted":False,
          "new_psf_reconstruction_performed":False,
          "native_pixels_or_catalog_modified":False}
    if x0<0 or y0<0 or x0+width>image.shape[1] or y0+width>image.shape[0]:
        return dict(base,status="EXPANDED_SCENE_OUTSIDE_NATIVE_FITS")
    sy,sx=slice(y0,y0+width),slice(x0,x0+width)
    z, inv=image[sy,sx],invvar[sy,sx]
    mask=valid[sy,sx]&np.isfinite(z)&np.isfinite(inv)&(inv>0)
    nmask=int(mask.sum())
    yy,xx=np.indices((width,width),dtype=float)
    xx+=x0;yy+=y0
    if not mask[half,half]:
        return dict(base,status="INVALID_NATIVE_CENTRAL_PIXEL")
    templates=[_shifted_native_kernel(k,xx,yy,cx,cy)]
    points=[]
    for source in point_sources:
        src=(float(source["x_pix"]),float(source["y_pix"]))
        t=_shifted_native_kernel(k,xx,yy,*src)
        if np.any(t[mask]>0.):
            templates.append(t)
            points.append(int(source["tractor_objid"]))
    n_point=len(points)
    # Avoid rendering dozens of expensive 3x3 subpixel-convolved
    # profiles before discovering that the source solve exceeds the
    # declared engineering compute budget. This is conservative:
    # a valid candidate may subsequently have no usable pixel support,
    # but the preflight must fail rather than silently discard sources.
    n_potential_extended=sum(
        audit_tractor_shape(source)["status"]
        == "VALID_APPROXIMATE_EXTENDED_TEMPLATE"
        and finite(source.get("flux_nMgy")) is not None
        and finite(source.get("flux_nMgy")) > 0.
        for source in extended_sources)
    if n_point+n_potential_extended>MAX_NUISANCE_SOURCES:
        return dict(
            base,
            status="POTENTIAL_EXTENDED_SOURCE_BUDGET_EXCEEDED_BEFORE_RENDER",
            n_point_templates_rendered=n_point,
            n_potential_shape_valid_extended_sources=n_potential_extended,
            n_original_extended_candidates=len(extended_sources),
            source_budget=MAX_NUISANCE_SOURCES,
            no_partial_catalog_source_truncation=True,
        )
    extended_audit=[]
    admitted=[]
    admitted_rows=[]
    for item in extended_sources:
        morphology=audit_tractor_shape(item)
        record={
            "tractor_objid":int(item["tractor_objid"]),
            "tractor_type":str(item["tractor_type"]),
            "separation_pix":float(math.hypot(
                item["x_pix"]-cx,item["y_pix"]-cy)),
            "catalog_flux_nMgy":finite(item.get("flux_nMgy")),
            "morphology":morphology}
        if morphology["status"]!="VALID_APPROXIMATE_EXTENDED_TEMPLATE":
            record["status"]="SKIPPED_NO_VERIFIED_MORPHOLOGY"
            extended_audit.append(record)
            continue
        if record["catalog_flux_nMgy"] is None or record["catalog_flux_nMgy"]<=0.:
            record["status"]="SKIPPED_MISSING_OR_NONPOSITIVE_FLUX"
            extended_audit.append(record)
            continue
        try:
            t=convolved_native_sersic(
                kernel=k,wcs=wcs,
                source_xy=(item["x_pix"],item["y_pix"]),
                origin_xy=(x0,y0),width=width,morphology=morphology)
        except (ArithmeticError,ValueError) as exc:
            record["status"]="SKIPPED_INVALID_PROFILE_RENDERING"
            record["reason"]=str(exc)
            extended_audit.append(record)
            continue
        if not np.any(t[mask]>0.):
            record["status"]="SKIPPED_NO_FINITE_TEMPLATE_OVERLAP"
            extended_audit.append(record)
            continue
        record["status"]="APPROXIMATE_TYPED_PROFILE_INCLUDED"
        extended_audit.append(record)
        templates.append(t)
        admitted.append(int(item["tractor_objid"]))
        admitted_rows.append(item)
    base.update({
        "n_catalog_extended_candidates":len(extended_sources),
        "n_extended_profiles_rendered":len(admitted),
        "n_extended_catalog_unmodeled":len(extended_sources)-len(admitted),
        "n_point_templates_rendered":n_point,
        "n_valid_original_native_pixels":nmask,
        "extended_source_morphology_audit":extended_audit,
        "all_catalog_extended_profiles_accounted_for":(
            len(admitted)==len(extended_sources)),
        "not_equivalent_to_tractor_gaussian_mixture_renderer":True,
        "finite_intrinsic_and_empirical_psf_support_unvalidated":True,
        "no_coadd_covariance_or_parameter_uncertainty_model":True,
        "profile_amplitudes_not_catalog_total_fluxes":True,
        "subpixel_integration_grid":SUBPIXEL_GRID,
        "source_count_budget":MAX_NUISANCE_SOURCES,
    })
    if n_point+len(admitted)>MAX_NUISANCE_SOURCES:
        return dict(base,status="TOO_MANY_RENDERED_SOURCES_NO_TRUNCATION")
    if not admitted:
        return dict(base,status="NO_VALID_EXTENDED_SOURCE_PROFILE_TO_TEST")
    if nmask<=len(templates)+3:
        return dict(base,status="INSUFFICIENT_NATIVE_PIXEL_DOF")
    if any(np.max(t[mask])<=0 for t in templates):
        return dict(base,status="DEGENERATE_NATIVE_PROFILE_SUPPORT")
    P=np.column_stack([t[mask]/np.max(t[mask]) for t in templates])
    B=np.column_stack((np.ones(nmask),
        ((xx-cx)/max(1.,half))[mask],((yy-cy)/max(1.,half))[mask]))
    w=np.sqrt(inv[mask])
    baseline_columns=1+n_point
    try:
        a,sky,rss,cond=_projected_nnls(z[mask],B,P,w)
        a0,sky0,rss0,cond0=_projected_nnls(
            z[mask],B,P[:,:baseline_columns],w)
    except (RuntimeError, ValueError) as exc:
        return dict(base,status="NUMERICALLY_UNIDENTIFIABLE_EXTENDED_SCENE",
                    reason=str(exc))
    residual=(z[mask]-B@sky-P@a)*w
    residual0=(z[mask]-B@sky0-P[:,:baseline_columns]@a0)*w
    distance=np.hypot(xx-cx,yy-cy)[mask]
    predictive_ring = _predeclared_ring_prediction(
        z[mask], B, P, w, distance,
        guessed_fwhm_pix=guessed_fwhm_pix,
        n_base_columns=baseline_columns)
    # One nearest already-rendered extended source is selected strictly
    # by catalog separation/OBJID, before inspecting any fit residual.
    # All other original rendered extended/PSF source columns are frozen
    # across the six one-at-a-time shape perturbations.
    chosen_index = min(
        range(len(admitted_rows)),
        key=lambda j: (
            math.hypot(float(admitted_rows[j]["x_pix"])-cx,
                       float(admitted_rows[j]["y_pix"])-cy),
            int(admitted_rows[j]["tractor_objid"])))
    shape_stress = _morphology_stress_p15d(
        z=z[mask], B=B, P=P, weights=w,
        native_radius=distance, mask=mask, kernel=k, wcs=wcs,
        scene_origin_xy=(x0,y0), scene_width_pix=width,
        guessed_fwhm_pix=guessed_fwhm_pix,
        source_row=admitted_rows[chosen_index],
        template_column=baseline_columns+chosen_index,
        n_rendered_extended_sources=len(admitted_rows))
    source_for_convergence = admitted_rows[chosen_index]
    p15e_sampling = audit_approximate_sersic_sampling(
        kernel=k, wcs=wcs,
        source_xy=(
            float(source_for_convergence["x_pix"]),
            float(source_for_convergence["y_pix"])),
        origin_xy=(x0,y0), width=width,
        morphology=audit_tractor_shape(source_for_convergence),
        valid=mask,
        baseline_template=templates[baseline_columns+chosen_index])
    p15e_sampling["selected_tractor_objid"] = int(
        source_for_convergence["tractor_objid"])
    p15e_sampling["selected_tractor_type"] = str(
        source_for_convergence["tractor_type"])
    p15e_sampling["source_selected_by_prefit_catalog_geometry"] = True
    regions={}
    for name,radius in [("one_guessed_fwhm",guessed_fwhm_pix),
                        ("four_guessed_fwhm",4*guessed_fwhm_pix)]:
        subset=distance<=radius
        u=float(np.dot(residual0[subset],residual0[subset]))
        v=float(np.dot(residual[subset],residual[subset]))
        regions[name]={
            "n_pixels":int(subset.sum()),
            "baseline_point_star_sky_rss":u,
            "typed_extended_star_point_sky_rss":v,
            "signed_rss_improvement_fraction": (
                float((u-v)/u) if u>0 else None),
            "same_original_native_pixels_globally_fit_models":True,
            "not_a_significance_or_psf_validation":True,
        }
    return dict(base,
        status="PARTIAL_APPROXIMATE_EXTENDED_SCENE_DIAGNOSTIC",
        fitted_extended_source_objids=admitted,
        weighted_rss_without_extended=float(rss0),
        weighted_rss_with_extended=float(rss),
        full_scene_fractional_rss_change=(
            float((rss0-rss)/rss0) if rss0>0 else None),
        central_aperture_comparison=regions,
        predeclared_2_to_4_fwhm_ring_prediction=predictive_ring,
        p15d_morphology_controlled_stress=shape_stress,
        p15e_analytic_subpixel_convergence=p15e_sampling,
        p15e_external_tractor_reference={
            "schema": "desidera.psf0-p15e-external-tractor-stamp-comparison/1",
            "status": "ORIGINAL_MATCHED_TRACTOR_REFERENCE_NOT_SUPPLIED",
            "requires_same_wcs_same_source_same_coadd_psf": True,
            "no_true_tractor_profile_equivalence_measured": True,
            "science_adopted": False,
        },
        sky_with_minus_without_extended_at_star_nMgy_pix=
            float(sky[0]-sky0[0]),
        main_star_fit_with_extended_peak_nMgy_pix=float(a[0]),
        main_star_fit_without_extended_peak_nMgy_pix=float(a0[0]),
        full_design_condition_number=float(cond),
        baseline_design_condition_number=float(cond0),
        nested_global_rss_gain_not_evidence_of_better_psf=True,
        omitted_catalog_sources_prevent_complete_deblending=(
            len(admitted)<len(extended_sources)),
    )
