"""Joint native-scene fitting must remain read-only, honest and non-adopting."""
from types import SimpleNamespace

import numpy as np
import pytest

from lsbpipe.photometry.psf_joint_scene import (
    _shifted_native_kernel, fit_fixed_psf_scene,
    audit_fixed_psf_joint_scenes, MAX_NUISANCE_SOURCES,
)


def _synthetic_field(width=201, sigma=3., *, center=(100.25, 99.65),
                     neighbor=(118.6, 100.4), source_flux=120.,
                     neighbor_flux=15., include_neighbor=True):
    ky, kx = np.indices((81, 81), dtype=float)
    kernel = np.exp(-.5*((kx-40)**2+(ky-40)**2)/sigma**2)
    kernel /= np.sum(kernel)
    yy, xx = np.indices((width, width), dtype=float)
    image = (.004 + .000015*(xx-center[0])
             - .000008*(yy-center[1]))
    image += source_flux * _shifted_native_kernel(
        kernel, xx, yy, center[0], center[1])
    if include_neighbor:
        image += neighbor_flux * _shifted_native_kernel(
            kernel, xx, yy, neighbor[0], neighbor[1])
    return image, kernel


def _source_neighbor(x=118.6, y=100.4):
    return {"x_pix":x, "y_pix":y, "tractor_objid":33,
            "tractor_type":"PSF", "flux_nMgy":15.}


def test_native_joint_scene_recovers_known_star_sky_neighbor_without_changing_input():
    image, kernel = _synthetic_field()
    original = image.copy()
    ivar = np.full_like(image, 1500.)
    mask = np.ones_like(image, dtype=bool)
    result = fit_fixed_psf_scene(
        image, ivar, mask, kernel=kernel,
        target_xy=(100.25,99.65),
        neighbors=[_source_neighbor()], guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.001)
    assert result["status"] == "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    assert result["scene_width_pix"] == 161
    assert result["n_original_native_pixels"] == 161**2
    assert result["target_fitted_model_support_flux_nMgy"] == pytest.approx(
        120.,rel=2e-3)
    assert result["neighbors"][0]["fitted_flux_within_model_support_nMgy"] == pytest.approx(
        15.,rel=2e-3)
    assert result["fitted_sky_at_star_nMgy_per_native_pixel"] == pytest.approx(
        .004,abs=1e-8)
    assert result["fitted_sky_gradient_x_nMgy_per_native_pixel_per_pix"] == pytest.approx(
        .000015,abs=1e-9)
    assert result["fitted_sky_gradient_y_nMgy_per_native_pixel_per_pix"] == pytest.approx(
        -.000008,abs=1e-9)
    assert result["joint_minus_annulus_sky_nMgy_per_native_pixel"] == pytest.approx(
        .003,abs=1e-8)
    assert result["weighted_squared_residual_star_neighbors_plane"] < 1e-10
    assert result["weighted_squared_residual_star_plane_only"] > 0.
    assert result["weighted_residual_improvement_fraction"] > .99
    local = result["central_aperture_residual_comparison"]
    for region in ("one_guessed_fwhm","four_guessed_fwhm"):
        value=local[region]
        assert value["status"]=="SAME_NATIVE_PIXELS_WITH_GLOBAL_PARAMETERS"
        assert value["n_native_pixels"] > 0
        assert value["weighted_squared_residual_full"] < 1e-10
        assert value["weighted_squared_residual_star_plane_only"] > 0.
        assert value["not_a_covariance_calibrated_or_independent_validation"]
    assert local["same_global_fit_coefficients_no_local_refit"]
    for region in ("entire_native_scene", "one_guessed_fwhm",
                   "four_guessed_fwhm"):
        part = local[region]
        gains = part["weighted_rss_gain_partition_by_fitted_model_change"]
        assert set(gains) == {
            "central_star_amplitude_reallocation",
            "planar_sky_reallocation",
            "direct_catalog_point_neighbors"}
        expected = (part["weighted_squared_residual_star_plane_only"]
                    - part["weighted_squared_residual_full"])
        assert sum(gains.values()) == pytest.approx(expected, rel=1e-10,
                                                    abs=1e-9)
        assert part["weighted_rss_gain_partition_closure_error"] == (
            pytest.approx(0., abs=1e-9))
        assert part[
            "gain_partition_is_algebraic_noncausal_with_symmetric_cross_terms"]
    assert result["star_only_null_fitted_peak_nMgy_per_pixel"] > 0
    assert result["neighbor_to_central_core_model_flux_ratio"] is not None
    assert result["neighbor_to_central_4fwhm_model_flux_ratio"] > 0.
    assert result["correlated_noise_and_fit_parameter_covariance_unmodeled"]
    assert result["kernel_support_beyond_observed_ePSF_unmodeled"]
    assert not result["science_adopted"]
    np.testing.assert_array_equal(image,original)
    np.testing.assert_array_equal(ivar,1500.)


def test_joint_scene_empty_neighbors_recovers_same_sky_plane():
    image, kernel = _synthetic_field(include_neighbor=False)
    result = fit_fixed_psf_scene(
        image, np.ones_like(image), np.ones_like(image,dtype=bool),
        kernel=kernel, target_xy=(100.25,99.65),
        neighbors=[],guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=0.)
    assert result["status"] == "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    assert result["n_catalog_point_neighbors"] == 0
    assert result["weighted_squared_residual_star_neighbors_plane"] == pytest.approx(
        result["weighted_squared_residual_star_plane_only"], abs=1e-10)


def test_joint_scene_refuses_masked_central_pixel_and_stamp_edge():
    image, kernel = _synthetic_field()
    valid = np.ones_like(image,dtype=bool)
    # Rounded central pixel must not be reconstructed or inpainted.
    valid[100,100] = False
    common=dict(kernel=kernel, neighbors=[], guessed_fwhm_pix=6.57,
                original_sky_nMgy_pix=0.)
    result=fit_fixed_psf_scene(
        image,np.ones_like(image),valid,
        target_xy=(100.25,99.65),**common)
    assert result["status"]=="INVALID_CENTRAL_NATIVE_PIXEL"
    result=fit_fixed_psf_scene(
        image,np.ones_like(image),np.ones_like(valid),
        target_xy=(60.,60.),**common)
    assert result["status"]=="FULL_EXPANDED_NATIVE_STAMP_OUTSIDE_IMAGE"


def test_joint_scene_refuses_more_than_bounded_source_budget_without_partial_fit():
    image,kernel=_synthetic_field()
    neighbors=[_source_neighbor(x=115.+i*.01) for i in range(
        MAX_NUISANCE_SOURCES+1)]
    result=fit_fixed_psf_scene(
        image,np.ones_like(image),np.ones_like(image,dtype=bool),
        target_xy=(100.25,99.65),kernel=kernel,
        neighbors=neighbors, guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.004)
    assert result["status"]=="TOO_MANY_PSF_NEIGHBORS_FOR_BOUNDED_FIT"
    assert result["science_adopted"] is False


def test_joint_scene_catalog_audit_keeps_original_holdout_and_non_psf_unmodeled():
    image,kernel=_synthetic_field()
    frame=SimpleNamespace(
        image_nMgy=image, invvar_nMgy2=np.ones_like(image),
        valid=np.ones_like(image,dtype=bool), mask_reasons={},
        band="g",dataset_id="dr11/south/abc/g",shape=image.shape)
    cat=SimpleNamespace(
        source_sha256="a"*64,
        dataset_id=frame.dataset_id,match_radius_pix=2.5,
        rows=(
            {"ref_cat":"G3","ref_id":10,"objid":14,"type":"PSF","flux":120.},
            {"ref_cat":"","ref_id":0,"objid":33,"type":"PSF","flux":15.},
            {"ref_cat":"","ref_id":0,"objid":34,"type":"DEV","flux":.1},
        ),
        pixel_positions=np.array(
            [[100.25,99.65],[118.6,100.4],[140.,110.]])
    )
    star=SimpleNamespace(
        star_id="star-0032",x_pix=100.25,y_pix=99.65,
        gaia_source_id=10,cutout_sky_nMgy=.001,role="holdout")
    result=audit_fixed_psf_joint_scenes(
        frame,kernel,[star],cat,
        protected_mask=np.zeros_like(image,dtype=bool),
        guessed_fwhm_pix=6.57)
    assert result["status"]=="FIXED_PSF_ORIGINAL_NATIVE_SCENE_AUDIT_NO_SCIENCE_ADOPTION"
    assert result["n_completed_fixed_holdouts"]==1
    row=result["per_star"][0]
    assert row["source_id"]=="star-0032"
    assert row["role"]=="holdout"
    assert row["n_unmodeled_extended_or_nonpositive_catalog_sources"]==1
    assert row["catalog_scene_fully_modelled"] is False
    assert row["known_unmodeled_catalog_sources_prevent_science_certification"] is True
    assert result["n_completed_scenes_with_unmodeled_catalog_sources"]==1
    assert row["nearest_unmodeled_catalog_source"]["tractor_type"]=="DEV"
    assert row["nearest_unmodeled_catalog_source"]["tractor_objid"]==34
    assert row["nearest_unmodeled_catalog_source"]["reason"]=="TRACTOR_NON_PSF_MODEL"
    assert row["catalog_point_neighbor_count_full_support"]==1
    assert row["neighbors"][0]["tractor_objid"]==33
    assert result["nuisance_parameter_fit_to_holdouts_not_blind_validation"]
    assert result["science_adopted"] is False


def test_joint_scene_gaia_mismatch_cannot_relabel_another_catalog_source():
    image,kernel=_synthetic_field()
    frame=SimpleNamespace(
        image_nMgy=image, invvar_nMgy2=np.ones_like(image),
        valid=np.ones_like(image,dtype=bool), mask_reasons={},
        band="g",dataset_id="dr11/south/abc/g",shape=image.shape)
    cat=SimpleNamespace(source_sha256="a"*64,
        dataset_id=frame.dataset_id,match_radius_pix=2.5,
        rows=({"ref_cat":"G3","ref_id":99,"objid":1,"type":"PSF",
               "flux":100.},),
        pixel_positions=np.array([[100.25,99.65]]))
    star=SimpleNamespace(
        star_id="star-0032",x_pix=100.25,y_pix=99.65,
        gaia_source_id=10,cutout_sky_nMgy=.001,role="holdout")
    report=audit_fixed_psf_joint_scenes(
        frame,kernel,[star],cat,
        protected_mask=np.zeros_like(image,dtype=bool),
        guessed_fwhm_pix=6.57)
    assert report["n_completed_joint_scene_fits"]==0
    assert report["per_star"][0]["status"]=="CENTRAL_GAIA_ID_MISMATCH"


def test_joint_scene_does_not_pretend_fixed_kernel_has_extended_wings():
    image,kernel=_synthetic_field()
    # The actual source has a known additional broad halo; the fitted
    # kernel lacks it. The diagnostic must never report wide-wing recovery.
    yy,xx=np.indices(image.shape,dtype=float)
    halo=np.exp(-.5*(
        (xx-100.25)**2+(yy-99.65)**2)/35.**2)
    original=image+halo*.0003
    result=fit_fixed_psf_scene(
        original,np.ones_like(original),np.ones_like(original,dtype=bool),
        kernel=kernel,target_xy=(100.25,99.65),
        neighbors=[_source_neighbor()],guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.004)
    assert result["status"]=="FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    assert result["kernel_support_beyond_observed_ePSF_unmodeled"]
    assert result["science_adopted"] is False


def test_joint_scene_gui_summary_has_no_false_science_adoption():
    pytest.importorskip("PySide6")
    from lsbpipe.gui.psf0_panel import PSF0Panel
    metrics={
        "fixed_psf_joint_native_scene_qa": {
            "status": "FIXED_PSF_ORIGINAL_NATIVE_SCENE_AUDIT_NO_SCIENCE_ADOPTION",
            "n_completed_joint_scene_fits": 1,
            "n_preselected_stars": 2,
            "n_completed_fixed_holdouts": 1,
            "n_holdouts_expected": 1,
            "p15_extended_scene_pilot_summary": {
                "n_numerical_partial_extended_fits":1,
                "n_attempted_after_p14":1,
            },
            "per_star": [
                {"source_id":"star-0032", "role":"holdout",
                 "status":"FIXED_PSF_JOINT_SCENE_DIAGNOSTIC",
                 "n_catalog_point_neighbors":1,
                 "neighbor_to_central_4fwhm_model_flux_ratio": .025,
                 "joint_minus_annulus_sky_nMgy_per_native_pixel":.0004,
                 "n_unmodeled_extended_or_nonpositive_catalog_sources":2,
                 "weighted_residual_improvement_fraction":.926,
                 "central_aperture_residual_comparison": {
                     "four_guessed_fwhm": {
                         "signed_improvement_fraction":-.013,
                         "weighted_squared_residual_star_plane_only":100.,
                         "weighted_rss_gain_partition_by_fitted_model_change": {
                             "central_star_amplitude_reallocation": -0.5,
                             "planar_sky_reallocation": -0.8,
                             "direct_catalog_point_neighbors":0.}}},
                 "nearest_unmodeled_catalog_source": {
                     "tractor_objid":12,
                     "tractor_type":"REX",
                     "separation_from_star_pix":18.60},
                 "n_catalog_neighbors_with_exact_zero_finite_psf_support":1,
                 "p15_extended_tractor_scene": {
                     "status":"PARTIAL_APPROXIMATE_EXTENDED_SCENE_DIAGNOSTIC",
                     "n_extended_profiles_rendered":1,
                     "n_catalog_extended_candidates":2,
                     "full_scene_fractional_rss_change":.41,
                     "central_aperture_comparison": {
                         "four_guessed_fwhm": {
                             "signed_rss_improvement_fraction":-.05}},
                     "sky_with_minus_without_extended_at_star_nMgy_pix":-.0006,
                     "predeclared_2_to_4_fwhm_ring_prediction": {
                         "status": "PREDECLARED_RING_OUT_OF_SAMPLE_PIXEL_PREDICTION",
                         "signed_predictive_ring_rss_improvement_fraction":-.12,
                         "n_reserved_native_pixels":244},
                     "p15e_analytic_subpixel_convergence": {
                         "status": "THREE_GRID_ANALYTIC_CONVERGENCE_MEASURED_NOT_TRACTOR",
                         "three_to_five": {"shape_l1_unit_flux_difference":.0023},
                         "five_to_seven": {"shape_l1_unit_flux_difference":.0007}},
                     "p15e_external_tractor_reference": {
                         "status": "ORIGINAL_MATCHED_TRACTOR_REFERENCE_NOT_SUPPLIED"},
                     "p15d_morphology_controlled_stress": {
                         "status": "PREDECLARED_MORPHOLOGY_STRESS_DIAGNOSTIC",
                         "selected_tractor_objid":1941,
                         "n_successful_stress_cases":6,
                         "descriptive_min_max_stress_response_not_error_bars": {
                             "delta_sky_at_star_nMgy_per_native_pixel": {
                                 "min":-.0008, "max":.0003},
                             "delta_star_fitted_peak_nMgy_per_pixel": {
                                 "min":-.001, "max":.002},
                             "delta_reserved_ring_weighted_rss": {
                                 "min":-.1, "max":1.4},
                         },
                     }}},                {"source_id":"star-0011", "role":"train",
                 "status":"FULL_EXPANDED_NATIVE_STAMP_OUTSIDE_IMAGE"},
            ]
        }
    }
    summary=PSF0Panel._calibration_diagnostic_summary(metrics)
    assert "1/2 stars" in summary
    assert "1/1 reserved Gaia holdouts" in summary
    assert "star-0032" in summary
    assert "2.5%" in summary
    assert "0.0004 nMgy/pix" in summary
    assert "2 unmodeled catalog sources" in summary
    assert "No science adoption" in summary
    assert "full scene +92.6%" in summary
    assert "central <=4 guessed FWHM -1.3%" in summary
    assert "central-star amplitude -0.5%" in summary
    assert "sky-plane change -0.8%" in summary
    assert "direct catalog-point templates +0%" in summary
    assert "symmetric cross-terms, noncausal" in summary
    assert "TYPE=REX" in summary
    assert "offset=18.60" in summary
    assert "zero support" in summary
    assert "P15 approximate Tractor REX/EXP/DEV" in summary
    assert "1/1 native scenes" in summary
    assert "1/2" in summary
    assert "global RSS +41%" in summary
    assert "central 4 FWHM -5%" in summary
    assert "sky shift -0.0006 nMgy/pix" in summary
    assert "HELD OUT from both fits" in summary
    assert "predictive weighted RSS change -12%" in summary
    assert "244 reserved native pixels" in summary
    assert "not independent PSF QA" in summary
    assert "P15D controlled morphology stress" in summary
    assert "P15E analytic Sérsic subpixel" in summary
    assert "3→5 L1=0.0023" in summary
    assert "5→7 L1=0.0007" in summary
    assert "NOT true Tractor rendering" in summary
    assert "ORIGINAL_MATCHED_TRACTOR_REFERENCE_NOT_SUPPLIED" in summary
    assert "no external profile comparison certified" in summary
    assert "OBJID=1941" in summary
    assert "6/6 one-at-a-time cases" in summary
    assert "Δsky [-0.0008, +0.0003]" in summary
    assert "Δcentral peak [-0.001, +0.002]" in summary
    assert "Δwithheld ring RSS [-0.1, +1.4]" in summary
    assert "NOT catalog errors" in summary
    assert "NOT a recovered wide PSF" in summary


def test_joint_scene_rejects_fractional_footprint_without_silent_truncation():
    image,kernel=_synthetic_field()
    with pytest.raises(ValueError,match="footprint"):
        fit_fixed_psf_scene(
            image,np.ones_like(image),np.ones_like(image,dtype=bool),
            kernel=kernel,target_xy=(100.25,99.65),
            neighbors=[],guessed_fwhm_pix=6.57,
            original_sky_nMgy_pix=.004,scene_size_pix=160.5)


def test_zero_support_catalog_psf_does_not_block_real_star_fit():
    image,kernel=_synthetic_field(include_neighbor=False)
    # 161x161 stamp centered on x=100.25 ends at x=180;
    # this catalogued PSF at x=221 has exactly zero frozen-kernel
    # support on *any* of its original native pixels.
    invisible=_source_neighbor(x=221.,y=100.4)
    invisible["tractor_objid"]=471
    result=fit_fixed_psf_scene(
        image,np.ones_like(image),np.ones_like(image,dtype=bool),
        kernel=kernel,target_xy=(100.25,99.65),
        neighbors=[invisible],guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.004)
    assert result["status"]=="FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    assert result["n_catalog_point_neighbors_in_expanded_search"]==1
    assert result["n_catalog_point_neighbors"]==0
    assert result["n_catalog_neighbors_with_exact_zero_finite_psf_support"]==1
    assert result["catalog_neighbors_with_exact_zero_finite_psf_support"][0][
        "tractor_objid"]==471
    assert result["zero_support_exclusion_is_only_relative_to_frozen_kernel"]
    assert result["weighted_squared_residual_star_neighbors_plane"]==pytest.approx(
        result["weighted_squared_residual_star_plane_only"],abs=1e-10)
    assert result["science_adopted"] is False


def test_central_aperture_residuals_are_not_global_scene_residuals():
    image,kernel=_synthetic_field(include_neighbor=False)
    yy,xx=np.indices(image.shape,dtype=float)
    far=(164.,100.4)
    image=image+85.*_shifted_native_kernel(
        kernel,xx,yy,far[0],far[1])
    rng=np.random.default_rng(17)
    image=image+rng.normal(0.,0.03,image.shape)
    far_source=_source_neighbor(x=far[0],y=far[1])
    result=fit_fixed_psf_scene(
        image,np.ones_like(image),np.ones_like(image,dtype=bool),
        kernel=kernel,target_xy=(100.25,99.65),
        neighbors=[far_source],guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.004)
    assert result["status"]=="FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    local=result["central_aperture_residual_comparison"]
    core=local["one_guessed_fwhm"]
    outer=local["four_guessed_fwhm"]
    assert core["n_native_pixels"] < outer["n_native_pixels"]
    assert outer["n_native_pixels"] < result["n_valid_native_pixels"]
    assert outer["weighted_squared_residual_full"] <= (
        result["weighted_squared_residual_star_neighbors_plane"])
    assert outer["weighted_squared_residual_star_plane_only"] <= (
        result["weighted_squared_residual_star_plane_only"])
    assert result["weighted_residual_improvement_fraction"] >= 0.
    assert -1. < outer["signed_improvement_fraction"] < 1.
    assert abs(outer["signed_improvement_fraction"]) < (
        result["weighted_residual_improvement_fraction"])
    assert result["comparison_fit_has_more_parameters_and_cannot_be_science_gate"]


def test_distant_catalog_psf_can_change_core_rss_only_through_global_sky_star_refit():
    image, kernel = _synthetic_field(include_neighbor=False)
    yy, xx = np.indices(image.shape, dtype=float)
    # Frozen-kernel support of this far source is entirely beyond 4FWHM,
    # but part of its footprint lies inside the global 161x161 patch.
    distant = (194.25, 99.65)
    image += 30.*_shifted_native_kernel(
        kernel, xx, yy, distant[0], distant[1])
    neighbor = _source_neighbor(x=distant[0], y=distant[1])
    result = fit_fixed_psf_scene(
        image, np.ones_like(image), np.ones_like(image, dtype=bool),
        kernel=kernel, target_xy=(100.25,99.65),
        neighbors=[neighbor], guessed_fwhm_pix=6.57,
        original_sky_nMgy_pix=.004)
    assert result["status"] == "FIXED_PSF_JOINT_SCENE_DIAGNOSTIC"
    local = result["central_aperture_residual_comparison"]
    full = local["entire_native_scene"]
    inner = local["four_guessed_fwhm"]
    assert full["weighted_squared_residual_star_plane_only"] > 0.
    assert full["weighted_squared_residual_full"] < 1e-10
    assert inner["direct_catalog_point_neighbors_model_power_weighted"] == 0.
    assert inner["weighted_rss_gain_partition_by_fitted_model_change"][
        "direct_catalog_point_neighbors"] == 0.
    assert sum(inner["weighted_rss_gain_partition_by_fitted_model_change"].values()
               ) == pytest.approx(
        inner["weighted_squared_residual_star_plane_only"]
        - inner["weighted_squared_residual_full"], abs=1e-9)
    assert inner["gain_partition_is_algebraic_noncausal_with_symmetric_cross_terms"]
    assert result["science_adopted"] is False
