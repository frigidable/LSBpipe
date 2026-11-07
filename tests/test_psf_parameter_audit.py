"""No PSF0 heuristic may silently become a validated scientific limit."""
import numpy as np
import pytest

from lsbpipe.photometry.psf_parameter_audit import (
    audit_psf0_parameters, audit_star_sky_ring_sensitivity,
    audit_gaia_catalog_seeds_vs_dao,
)


def test_all_existing_psf0_thresholds_explicitly_classified():
    pytest.importorskip("astropy")
    from lsbpipe.photometry.psf_empirical import PSF0Config
    from lsbpipe.photometry.psf_gui_backend import psf0_experiment_recipe
    cfg = psf0_experiment_recipe(6.57, "outer_sky_isolation_footprint")
    report = audit_psf0_parameters(cfg)
    assert set(report["parameters"]) == set(vars(cfg))
    assert report["parameters"]["sky_annulus_inner_pix"]["basis"] == "HEURISTIC_GEOMETRY"
    assert report["parameters"]["guessed_fwhm_pix"]["basis"] == "OBSERVATIONAL_PRIOR_OR_MANUAL"
    assert report["geometry"]["ring_circles_disjoint_at_isolation_distance"] is True
    assert report["geometry"]["square_stamps_can_overlap_at_isolation_distance"] is True
    assert report["automatic_parameter_science_certification"] is False
    assert report["parameters"]["min_holdout_stars"]["calibrated_from_original_fits"] is False


def test_sky_ring_probe_detects_injected_wings_without_autotuning():
    yy, xx = np.indices((81, 81), dtype=float)
    rr = np.hypot(xx-40, yy-40)
    # This synthetic broad halo contaminates nearer sky probes.  It is not
    # intended as a realistic Leo P or DECam PSF.
    original = 4. + 20*np.exp(-.5*(rr/12.)**2)
    original_before = original.copy()
    valid = np.ones_like(original, dtype=bool)
    output = audit_star_sky_ring_sensitivity(
        original, valid, center_xy=(40., 40.),
        inner_radius=30., outer_radius=38.,
        reference_sky_nMgy_pix=4., reference_rms_nMgy_pix=.1,
        guessed_fwhm_pix=6.57)
    assert output["status"].startswith("ORIGINAL_PIXELS")
    assert output["max_absolute_inner_ring_sky_change_in_local_rms"] > 1.
    assert output["changes_psf_selection_or_sky_model"] is False
    assert output["outer_ring_beyond_cutout_not_tested"] is True
    scan = output["one_pixel_inner_boundary_sensitivity_scan"]
    assert len(scan) == 30-int(np.ceil(6.57))+1
    assert [item["inner_boundary_pix"] for item in scan] == list(
        range(int(np.ceil(6.57)), 31))
    assert scan[-1]["inner_boundary_pix"] == 30
    assert output["scan_not_an_automatic_sky_ring_optimizer"] is True
    assert output["scan_entries_nested_and_not_statistically_independent"] is True
    np.testing.assert_array_equal(original, original_before)


def test_sky_ring_probe_stable_for_exact_planar_sky():
    yy, xx = np.indices((81,81),dtype=float)
    # Tiny deterministic nonzero fluctuations keep the robust RMS defined.
    img = (3. + .01*(xx-40) - .02*(yy-40)
           + .0002*np.sin(.2*xx)*np.cos(.35*yy))
    result = audit_star_sky_ring_sensitivity(
        img, np.ones_like(img,dtype=bool),
        center_xy=(40.,40.), inner_radius=30, outer_radius=38,
        reference_sky_nMgy_pix=3., reference_rms_nMgy_pix=.1,
        guessed_fwhm_pix=6.57)
    assert len(result["probes"]) == 2
    assert all(item["status"]=="FIT_FOR_SENSITIVITY_ONLY" for item in result["probes"])
    assert result["max_absolute_inner_ring_sky_change_in_local_rms"] < .02


def test_sky_ring_probes_refuse_invalid_input():
    with pytest.raises(ValueError):
        audit_star_sky_ring_sensitivity(
            np.ones((20,20)), np.ones((10,10),dtype=bool),
            center_xy=(10,10),inner_radius=4,outer_radius=9,
            reference_sky_nMgy_pix=1.,reference_rms_nMgy_pix=1.,
            guessed_fwhm_pix=4.)


class FakeMatcher:
    dataset_id = "dr11/south/1554p180/g"
    match_radius_pix = 2.5
    rows = (
        {"ref_cat":"G3","ref_id":1,"type":"PSF"},
        {"ref_cat":"G3","ref_id":2,"type":"PSF"},
        {"ref_cat":"G3","ref_id":3,"type":"PSF"},
        {"ref_cat":"G3","ref_id":4,"type":"PSF"},
        {"ref_cat":"G3","ref_id":4,"type":"PSF"},
    )
    pixel_positions = np.array([[50.,50.],[130.,50.],[210.,50.],
                                [270.,50.],[275.,50.]])
    def match(self,x,y,*,band,neighbor_search_radius_pix,require_gaia):
        if x == 210.:
            return None, "TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT"
        return {"gaia_source_id":int(x)}, None


def test_gaia_only_catalog_preflight_does_not_promote_or_remove_stars():
    m = FakeMatcher()
    result = audit_gaia_catalog_seeds_vs_dao(
        m, [(50.7,50.2)], frame_shape=(350,350),
        protected=np.zeros((350,350),dtype=bool),band="g",
        cutout_size_pix=81,guessed_fwhm_pix=6.57)
    assert result["n_catalog_gaia_type_psf_rows"] == 5
    assert result["n_catalog_only_preflight_pass"] == 3
    assert result["n_near_dao_centroid_not_proven_associated"] == 1
    assert result["n_catalog_preflight_without_near_dao"] == 2
    assert result["preflight_rejected_reasons"]["DUPLICATE_GAIA_SOURCE_ID"] == 1
    assert result["preflight_rejected_reasons"]["TRACTOR_NEIGHBOR_WITHIN_PSF_CUTOUT"] == 1
    assert result["no_changes_to_stellar_sample_or_holdouts"] is True
    assert result["science_adopted"] is False


def test_catalog_preflight_does_not_confuse_galaxy_with_valid_seed():
    m = FakeMatcher()
    mask=np.zeros((350,350),dtype=bool)
    mask[50,130]=True
    result = audit_gaia_catalog_seeds_vs_dao(
        m, np.empty((0,2)),frame_shape=mask.shape,protected=mask,
        band="g",cutout_size_pix=81,guessed_fwhm_pix=6.57)
    assert result["preflight_rejected_reasons"]["GALAXY_PROTECTED_AT_CATALOG_SEED"]==1
    assert result["n_near_dao_centroid_not_proven_associated"]==0


def test_gui_calibration_summary_is_compact_and_keeps_full_qa_in_report():
    pytest.importorskip("PySide6")
    from lsbpipe.gui.psf0_panel import PSF0Panel
    catalog = {
        "candidate_screening_audit": [{"star_id":"star-0001"}],
        "parameter_provenance_qa": {"parameters": {"giant": [1]*100}},
        "gaia_catalog_seed_vs_dao_qa": {
            "n_catalog_only_preflight_pass": 23,
            "n_catalog_preflight_without_near_dao": 7,
            "catalog_preflight_without_dao_details": [{"gaia_source_id":i} for i in range(10)]},
    }
    metrics = {
        "parameter_provenance_qa": {"status": "NOT_CALIBRATED"},
        "catalog_selection_qa": catalog,
        "sky_sample_qa": {"per_star": [
            {"inner_ring_sensitivity_diagnostic": {
                "max_absolute_inner_ring_sky_change_in_local_rms": 1.2}},
            {"inner_ring_sensitivity_diagnostic": {
                "max_absolute_inner_ring_sky_change_in_local_rms": 0.8}},
        ]},
    }
    compact = PSF0Panel._catalog_screening_summary(catalog)
    assert "catalog_preflight_without_dao_details" not in compact
    assert "giant" not in compact
    text = PSF0Panel._calibration_diagnostic_summary(metrics)
    assert "23 candidates" in text
    assert "7 without a DAO centroid" in text
    assert "median maximum |delta sky| / local RMS = 1" in text
    assert "NOT scientific adoption" in text
    metrics["final_holdout_details_after_clipping"] = [
        {"core_normalized_aperture_growth": {
            "radial_sky_equivalence_consistency": {
                "span_over_local_pixel_rms": 1.4},
            "radii": [{
                "radius_in_guessed_fwhm": 4.,
                "status": "FULL_NATIVE_APERTURE",
                "equivalent_uniform_sky_offset_over_local_pixel_rms": -0.2
            }]}},
        {"core_normalized_aperture_growth": {"radii": [{
            "radius_in_guessed_fwhm": 4.,
            "status": "MASKED_APERTURE_DIAGNOSTIC_ONLY",
            "equivalent_uniform_sky_offset_over_local_pixel_rms": 0.4
        }]}},
    ]
    summary = PSF0Panel._calibration_diagnostic_summary(metrics)
    assert "1 full-aperture stars" in summary
    assert "median |offset|/local pixel RMS 0.2" in summary
    assert "NOT an estimated actual sky bias" in summary
    assert "median span / local pixel RMS 1.4" in summary
    assert "NOT a calibrated test of a constant sky" in summary


def test_holdout_growth_reports_exact_uniform_sky_equivalent_without_correcting_pixels():
    from lsbpipe.photometry.psf_holdout_qa import _core_normalized_growth
    y,x=np.indices((81,81),dtype=float)
    radius=np.hypot(x-40,y-40)
    model=np.exp(-.5*(radius/3.)**2)
    injected_background=0.007
    science=model+injected_background
    initial=science.copy()
    report=_core_normalized_growth(
        science,model,np.ones((81,81),dtype=bool),radius,
        center_xy=(40.,40.),guessed_fwhm_pix=6.57,
        amplitude=1.,local_sky_rms_nMgy_pix=0.035)
    assert report["uniform_sky_equivalent_is_algebra_not_sky_calibration"] is True
    assert report["radii"][0]["equivalent_uniform_sky_offset_nMgy_per_native_pixel"] is None
    for row in report["radii"][1:]:
        assert row["status"]=="FULL_NATIVE_APERTURE"
        assert row["equivalent_uniform_sky_offset_nMgy_per_native_pixel"]==pytest.approx(
            injected_background,abs=1e-12)
        assert row["equivalent_uniform_sky_offset_over_local_pixel_rms"]==pytest.approx(
            .2,abs=1e-12)
    radial=report["radial_sky_equivalence_consistency"]
    assert radial["n_usable_radii_above_core"]==4
    assert radial["span_of_equivalent_offsets_nMgy_per_native_pixel"]==pytest.approx(
        0.,abs=1e-12)
    assert radial["span_over_local_pixel_rms"]==pytest.approx(0.,abs=1e-11)
    assert radial["not_a_chi_square_or_statistical_significance"] is True
    np.testing.assert_array_equal(science,initial)


def test_masked_holdout_does_not_claim_unbiased_sky_equivalence():
    from lsbpipe.photometry.psf_holdout_qa import _core_normalized_growth
    yy,xx=np.indices((81,81),dtype=float)
    r=np.hypot(xx-40,yy-40)
    model=np.exp(-.5*(r/3.)**2)
    valid=np.ones((81,81),dtype=bool)
    valid[40,52]=False   # r=12: outside 1.5 FWHM, inside 2/3/4 FWHM
    report=_core_normalized_growth(
        model+.01,model,valid,r,center_xy=(40.,40.),
        guessed_fwhm_pix=6.57,amplitude=1.)
    row=next(v for v in report["radii"] if v["radius_in_guessed_fwhm"]==3.)
    assert row["status"]=="MASKED_APERTURE_DIAGNOSTIC_ONLY"
    assert row["equivalent_uniform_sky_offset_nMgy_per_native_pixel"] is None
    assert row["equivalent_uniform_sky_offset_over_local_pixel_rms"] is None
    radial=report["radial_sky_equivalence_consistency"]
    assert radial["n_usable_radii_above_core"]==1
    assert radial["status"]=="INSUFFICIENT_COMPLETE_APERTURES"
    assert radial["span_over_local_pixel_rms"] is None


def test_nonuniform_wings_leave_cross_radius_sky_equivalent_spread():
    from lsbpipe.photometry.psf_holdout_qa import _core_normalized_growth
    yy,xx=np.indices((81,81),dtype=float)
    radius=np.hypot(xx-40,yy-40)
    model=np.exp(-.5*(radius/3.)**2)
    extended_wings=.01*np.exp(-.5*(radius/8.)**2)
    observed=model+extended_wings+.002
    report=_core_normalized_growth(
        observed,model,np.ones_like(observed,dtype=bool),radius,
        center_xy=(40.,40.),guessed_fwhm_pix=6.57,
        amplitude=1.,local_sky_rms_nMgy_pix=.03)
    radial=report["radial_sky_equivalence_consistency"]
    assert radial["status"]=="RADIAL_SENSITIVITY_ONLY"
    assert radial["n_usable_radii_above_core"]==4
    assert radial["span_over_local_pixel_rms"] > 0.
    assert radial["constant_sky_cannot_be_tested_without_covariance"] is True
