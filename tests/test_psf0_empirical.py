"""PSF-0 synthetic, source-isolated regression: no fabricated Leo P measurement."""
import json
import numpy as np
import pytest

pytest.importorskip("astropy")
pytest.importorskip("photutils")
from astropy.wcs import WCS

from lsbpipe.photometry.contracts import NativeBandFrame
from lsbpipe.photometry.psf_empirical import (
    PSF0Config, STATUS, build_empirical_coadd_psf)
from lsbpipe.photometry.psf_export import export_psf0_diagnostic


LEO_P = (155.438012, 18.088025)


def pilot_wcs(shape=(280,280), *, band="r"):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [shape[1]/2.+.5, shape[0]/2.+.5]
    wcs.wcs.crval = list(LEO_P)
    wcs.wcs.cd = np.array([[-.262,0],[0,.262]])/3600.
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def fake_frame(*, band="r", nstars="many", amplitude=145., bad=False,
               instrument_region="south"):
    rng = np.random.default_rng(173)
    shape = (280,280)
    yy,xx = np.indices(shape, dtype=float)
    img = 1.2 + 0.00013*xx + rng.normal(0, .28, shape)
    sites = [(x, y) for y in (35,78,122,166,209,248)
             for x in (35,78,122,166,209,248)]
    if nstars == "few":
        sites = sites[:5]
    if nstars == "none":
        sites = []
    for j,(x0,y0) in enumerate(sites):
        # Deliberately noninteger subpixel centroids and variable stellar flux.
        x0 += .12 if j%2 else -.24
        y0 += .29 if j%3 else -.19
        flux = amplitude*(1+0.15*(j%3))
        img += flux*np.exp(-.5*((xx-x0)**2+(yy-y0)**2) / (2.12**2))
    # A galaxy-like feature near the ICRS target must be excluded
    # from stellar PSF training even if its knots are bright.
    img += 21.*np.exp(-.5*((xx-140)**2+(yy-140)**2)/ (8.**2))
    ivar = np.ones(shape)*1./(.28**2)
    if bad:
        ivar[35,35] = 0.
    return NativeBandFrame(
        image_nMgy=img, invvar_nMgy2=ivar,
        nexp=np.ones(shape)*2.,
        maskbits=np.zeros(shape, dtype=np.int32),
        native_wcs=pilot_wcs(shape),
        release="dr11", region=instrument_region,
        brick="1554p180", band=band,
        source_evidence={"synthetic":True,
                         "data_status":"SYNTHETIC_NOT_OBSERVATIONAL_FITS"})


@pytest.fixture(scope="module")
def built():
    return build_empirical_coadd_psf(
        fake_frame(), target_icrs_deg=LEO_P,
        target_protection_arcsec=13.,
        config=PSF0Config(min_neighbor_separation_pix=32., min_train_stars=6,
                          min_holdout_stars=2, max_training_stars=30))


def test_returns_normalized_native_grid_epsf_with_disjoint_holdouts(built):
    result = built
    assert result.status == STATUS
    assert result.kernel.shape == (31,31)
    assert result.kernel.sum() == pytest.approx(1.,abs=1e-10)
    assert result.kernel.min() >= 0
    assert not result.kernel.flags.writeable
    with pytest.raises(ValueError):
        result.kernel.setflags(write=True)
    assert len(result.training) >= 6
    assert len(result.holdout) >= 2
    assert {s.star_id for s in result.training}.isdisjoint(
        {s.star_id for s in result.holdout})
    assert result.metrics["holdout_median_abs_core_residual_over_peak"] < .12
    assert result.metrics["signed_holdout_coverage_qa"]["status"] == "COMPLETE"
    assert result.metrics["signed_holdout_coverage_qa"][
        "n_expected_holdout_stars"] == len(result.holdout)
    # The signed precursor and the delivered (clipped/renormalized)
    # nonnegative model must BOTH be scored on the fixed holdout
    # inventory. No scientific acceptance is inferred from these scores.
    assert result.metrics["final_holdout_coverage_after_clipping"][
        "status"] == "COMPLETE"
    assert result.metrics["final_holdout_coverage_after_clipping"][
        "evaluated_kernel"] == "FINAL_NONNEGATIVE_NORMALIZED"
    assert result.metrics["final_holdout_coverage_after_clipping"][
        "expected_source_ids"] == result.metrics[
        "signed_holdout_coverage_qa"]["expected_source_ids"]
    assert result.metrics["final_holdout_summary_after_clipping"][
        "kernel_representation"] == "FINAL_NONNEGATIVE_NORMALIZED"
    assert len(result.metrics["final_holdout_details_after_clipping"]) == len(
        result.holdout)
    assert result.metrics["final_holdout_failures_after_clipping"] == []
    # New aperture growth is diagnostic-only: compare the actual signed
    # precursor and delivered model on the same holdouts without changing
    # selection, acceptance or their source-identities.
    sg = result.metrics["signed_holdout_core_normalized_flux_growth_summary"]
    fg = result.metrics[
        "final_holdout_core_normalized_flux_growth_summary_after_clipping"]
    assert sg["n_fixed_holdouts"] == fg["n_fixed_holdouts"] == len(result.holdout)
    assert sg["status"] == fg["status"] == "DESCRIPTIVE_NOT_SCIENCE_ADOPTED"
    assert len(sg["radii"]) == len(fg["radii"]) == 5
    assert sg["no_threshold_or_science_adoption_implication"] is True
    assert fg["no_threshold_or_science_adoption_implication"] is True
    for metric in (sg, fg):
        assert all(item["n_full_aperture_holdouts"] <= len(result.holdout)
                   for item in metric["radii"])
    for signed, final in zip(result.metrics["signed_holdout_details"],
                             result.metrics["final_holdout_details_after_clipping"]):
        assert signed["source_id"] == final["source_id"]
        assert set(signed["radial_residuals"]) == set(final["radial_residuals"])
        for zone in signed["radial_residuals"]:
            assert (signed["radial_residuals"][zone]["n_valid_pixels"]
                    == final["radial_residuals"][zone]["n_valid_pixels"])
    assert all(row["subpixel_center_consistent_with_model_shift"]
               for row in result.metrics["holdout_diagnostics"])
    assert result.lineage_id is None
    assert "SYNTHETIC_TEST_INPUT" in result.flags
    assert "CORE_PSF_ONLY_EXTENDED_WINGS_UNVALIDATED" in result.flags
    assert "SPATIAL_REPRESENTATIVITY_NOT_VALIDATED" in result.flags
    assert result.instrument == "DECAM"
    # DAO detections have NO native Tractor or Gaia identity. The fixed
    # holdout is computationally disjoint, but must not be misreported as
    # an independently Gaia-vetted stellar population.
    population = result.metrics["catalog_selection_qa"]
    assert population["policy"] == "dao_diagnostic"
    assert population["n_train_gaia_matched"] == 0
    assert population["n_holdout_gaia_matched"] == 0
    assert population["n_train_tractor_only"] == 0
    assert population["n_holdout_tractor_only"] == 0
    assert population["n_train_unverified_dao"] == len(result.training)
    assert population["n_holdout_unverified_dao"] == len(result.holdout)
    assert population["holdout_identity_validation"] == "NONE_DAO_CANDIDATES_ONLY"
    assert population["holdout_independent_of_epsf_fit"] is True
    candidates = population["candidate_screening_audit"]
    assert len([row for row in candidates
                if row.get("final_selection_role") == "DAO_UNVERIFIED_HOLDOUT"
               ]) == len(result.holdout)
    assert not any(row.get("final_selection_role") == "GAIA_HOLDOUT"
                   for row in candidates)
    assert result.metrics["implementation_method_sha256"] == (
        result.algorithm_versions["implementation_method_sha256"])
    assert result.report()["implementation_method_sha256"] == (
        result.metrics["implementation_method_sha256"])
    assert result.algorithm_versions["recipe"].endswith("/2")
    # A DAO-only synthetic result does NOT acquire invented catalog
    # neighbors, sky covariance or new science validation.
    joint=result.metrics["fixed_psf_joint_native_scene_qa"]
    assert joint["status"]=="NO_NATIVE_TRACTOR_CATALOG"
    assert joint["n_star_rows"]==len(result.training)+len(result.holdout)
    assert joint["science_adopted"] is False


def test_approximate_core_profile_and_no_galaxy_centroids(built):
    kernel = built.kernel
    c = kernel.shape[0]//2
    # A reasonably resolved Gaussian core should dominate outer pixels.
    assert kernel[c,c] > 8.*kernel[c+7,c]
    assert kernel[c,c] > kernel[0,0]
    assert all(np.hypot(star.x_pix-140,star.y_pix-140) > 13.
               for star in built.training+built.holdout)
    # No psf wing accuracy or galaxy measurement is claimed.
    report = built.report()
    assert report["science_adopted"] is False
    assert report["psf_wings_validated"] is False
    assert report["noise_covariance_validated"] is False
    assert report["n_train"] == len(built.training)
    assert report["n_holdout"] == len(built.holdout)
    assert report["kernel_sha256"]
    json.dumps(report, allow_nan=False)


def test_other_band_produces_own_identity_not_cloned_kernel(built):
    g = build_empirical_coadd_psf(
        fake_frame(band="g"), target_icrs_deg=LEO_P,
        target_protection_arcsec=13.)
    assert g.dataset_id.endswith("/g") and built.dataset_id.endswith("/r")
    assert g.lineage_id is None
    assert g.report()["status"] == STATUS


def test_no_data_or_few_sources_blocks_psf():
    for population in ("few", "none"):
        with pytest.raises(ValueError, match="star|stellar|source|candidates|detected"):
            build_empirical_coadd_psf(
                fake_frame(nstars=population), target_icrs_deg=LEO_P,
                target_protection_arcsec=13.)


def test_source_quality_rejects_invalid_and_protected_pixels():
    frame = fake_frame(bad=True)
    result = build_empirical_coadd_psf(
        frame, target_icrs_deg=LEO_P, target_protection_arcsec=13.)
    assert not frame.valid[35,35]
    assert result.status == STATUS
    assert not any(abs(star.x_pix-35)<2 and abs(star.y_pix-35)<2
                   for star in result.training+result.holdout)


def test_synthetic_psf_must_not_be_exported_as_observational_fits(tmp_path,built):
    with pytest.raises(ValueError, match="Explicit researcher"):
        export_psf0_diagnostic(built, tmp_path)
    with pytest.raises(ValueError, match="SHA-256-verified"):
        export_psf0_diagnostic(
            built, tmp_path, explicitly_requested=True)
    assert not list(tmp_path.glob("*"))


def test_invalid_recipe_and_wrong_input_fail_closed():
    with pytest.raises(ValueError, match="oversampling"):
        PSF0Config(psf_oversampling=2)
    with pytest.raises(ValueError, match="Star cutout"):
        PSF0Config(cutout_size_pix=30)
    with pytest.raises(TypeError, match="NativeBandFrame"):
        build_empirical_coadd_psf(
            np.zeros((30,30)), target_icrs_deg=LEO_P,
            target_protection_arcsec=90.)
    with pytest.raises(ValueError, match="Invalid ICRS"):
        build_empirical_coadd_psf(
            fake_frame(), target_icrs_deg=(410.,18.),
            target_protection_arcsec=90.)


def test_rejected_epsf_retains_star_diagnostics_without_export(monkeypatch):
    """A failed ePSF must not be quietly clipped or accepted for Leo P."""
    from types import SimpleNamespace
    import photutils.psf
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    class RingingBuilder:
        def __init__(self, **kwargs):
            self.shape = kwargs["shape"]

        def __call__(self, stars):
            kernel = np.zeros(self.shape, dtype=float)
            c = self.shape[0] // 2
            kernel[c, c] = 1.
            kernel[c+5, c] = -.2
            return SimpleNamespace(
                epsf=SimpleNamespace(data=kernel), converged=True)

    monkeypatch.setattr(photutils.psf, "EPSFBuilder", RingingBuilder)
    with pytest.raises(PSF0QualityError) as raised:
        build_empirical_coadd_psf(
            fake_frame(), target_icrs_deg=LEO_P,
            target_protection_arcsec=13., config=PSF0Config(
                min_train_stars=6, min_holdout_stars=2,
                max_training_stars=30))
    diagnostic = raised.value.diagnostics
    assert diagnostic["status"] == "PSF0_QUALITY_REJECTED_NOT_ADOPTED"
    assert diagnostic["negative_absolute_mass_fraction"] > .025
    assert diagnostic["n_train"] >= 6
    assert diagnostic["n_holdout"] >= 2
    assert diagnostic["training"]
    assert diagnostic["holdout"]
    assert len(diagnostic["training"]) == diagnostic["n_train"]
    assert "STRONG_NEGATIVE_EPSF_RINGING" == diagnostic["reason_code"]


def test_photutils31_wing_smoothing_ab_is_executed_and_audited():
    import inspect
    from photutils.psf import EPSFBuilder
    from lsbpipe.photometry.psf_empirical import PSF0QualityError
    if "wing_smoothing" not in inspect.signature(EPSFBuilder).parameters:
        pytest.skip("photutils <3.1 lacks native wing_smoothing option")
    config = PSF0Config(
        wing_smoothing=False, min_train_stars=6,
        min_holdout_stars=2, max_training_stars=30)
    try:
        proposal = build_empirical_coadd_psf(
            fake_frame(), target_icrs_deg=LEO_P,
            target_protection_arcsec=13., config=config)
    except PSF0QualityError as exc:
        diagnostic = exc.diagnostics
        assert diagnostic["status"] == "PSF0_QUALITY_REJECTED_NOT_ADOPTED"
        solver = diagnostic["solver_diagnostics"]
        assert diagnostic["psf_recipe"]["wing_smoothing"] is False
    else:
        assert proposal.status == STATUS
        solver = proposal.metrics["solver_diagnostics"]
        assert proposal.recipe.wing_smoothing is False
    assert solver["wing_smoothing_requested"] is False
    assert solver["wing_smoothing_explicitly_set"] is True
    assert solver["max_iterations_requested"] == 8
    assert solver["photutils_version"]
    history = solver["iteration_history_if_reported"]
    assert isinstance(history, list)
    assert history, "photutils 3.1 must expose actual ePSF iteration evidence"
    assert all("iteration" in step for step in history)
    assert len(history) <= 25
    assert "converged_if_reported" in solver


def test_manual_training_star_review_recomputes_without_touching_holdouts():
    from dataclasses import replace
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    frame = fake_frame()
    base = PSF0Config(
        min_train_stars=6, min_holdout_stars=2,
        max_training_stars=30, min_neighbor_separation_pix=32.)
    def run(cfg):
        try:
            outcome = build_empirical_coadd_psf(
                frame, target_icrs_deg=LEO_P,
                target_protection_arcsec=13., config=cfg)
            return outcome.training, outcome.holdout, outcome.review_snapshot
        except PSF0QualityError as exc:
            from types import SimpleNamespace
            train = [SimpleNamespace(**star) for star in
                     exc.diagnostics["training"]]
            holdout = [SimpleNamespace(**star) for star in
                       exc.diagnostics["holdout"]]
            return train, holdout, exc.review_snapshot

    train0, holdout0, review0 = run(base)
    assert review0["schema"] == "desidera.psf0-star-review-in-memory/1"
    assert len(review0["stars"]) == len(train0)
    assert all(s["cutout"].shape == (31, 31) for s in review0["stars"])
    assert all(s["model"].shape == s["cutout"].shape
               for s in review0["stars"])
    to_remove = train0[0].star_id
    excluded_cfg = replace(base, excluded_training_star_ids=(to_remove,))
    train1, holdout1, review1 = run(excluded_cfg)
    assert len(train1) == len(train0)-1
    assert {s.star_id for s in holdout0} == {s.star_id for s in holdout1}
    assert to_remove not in {s.star_id for s in train1}
    assert {s["star_id"] for s in review1["stars"]} == {
        s["star_id"] for s in review0["stars"]}
    assert next(s for s in review1["stars"]
                if s["star_id"] == to_remove)["role"] == "excluded"
    assert base.digest != excluded_cfg.digest
    with pytest.raises(ValueError, match="training sample|protected holdouts"):
        build_empirical_coadd_psf(
            frame, target_icrs_deg=LEO_P, target_protection_arcsec=13.,
            config=replace(base, excluded_training_star_ids=(
                holdout0[0].star_id,)))


def test_manual_exclusion_recipe_rejects_malformed_or_duplicated_ids():
    from dataclasses import replace
    with pytest.raises(ValueError, match="sorted, unique"):
        PSF0Config(excluded_training_star_ids=("star-0001", "star-0001"))
    with pytest.raises(ValueError, match="sorted, unique"):
        PSF0Config(excluded_training_star_ids=("star-2",))
    with pytest.raises(ValueError, match="sorted, unique"):
        PSF0Config(excluded_training_star_ids=("star-0003", "star-0002"))


def test_gaia_strict_requires_catalog_and_preserves_holdout_when_retraining(built):
    from dataclasses import replace
    from lsbpipe.photometry.psf_tractor_gaia import TractorGaiaMatcher
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    frame = fake_frame()
    base = PSF0Config(
        min_neighbor_separation_pix=32., min_train_stars=6,
        min_holdout_stars=2,max_training_stars=30,
        star_catalog_policy="gaia_strict")
    with pytest.raises(ValueError,match="GAIA_STRICT_REQUIRES_NATIVE_DR11_TRACTOR"):
        build_empirical_coadd_psf(
            frame,target_icrs_deg=LEO_P,target_protection_arcsec=13.,
            config=base)

    references = list(built.training) + list(built.holdout)
    rows = tuple({
        "ref_cat":"G3","ref_id":int(1000+i),
        "objid":i,"type":"PSF","fitbits":0,
        "maskbits":0,"gaia_duplicated_source":False,
        "flux":4.,"flux_ivar":100.,"gaia_g_mag":18.,
    } for i,_ in enumerate(references))
    positions=np.array([(s.x_pix,s.y_pix) for s in references])
    matcher=TractorGaiaMatcher(
        "synthetic-tractor.fits","c"*64,frame.dataset_id,rows,positions)

    def run(recipe):
        try:
            result=build_empirical_coadd_psf(
                frame,target_icrs_deg=LEO_P,target_protection_arcsec=13.,
                config=recipe,catalog_matcher=matcher)
            return result.training, result.holdout, result.review_snapshot
        except PSF0QualityError as exc:
            from types import SimpleNamespace
            return (
                [SimpleNamespace(**x) for x in exc.diagnostics["training"]],
                [SimpleNamespace(**x) for x in exc.diagnostics["holdout"]],
                exc.review_snapshot)
    train,holdout,snapshot=run(base)
    assert len(train)>=base.min_train_stars
    assert len(holdout)>=base.min_holdout_stars
    assert all(s.gaia_source_id is not None for s in train+holdout)
    assert snapshot["catalog_sha256"]=="c"*64
    assert snapshot["catalog_policy"]=="gaia_strict"
    assert all(item["gaia"]["gaia_source_id"]>0 for item in snapshot["stars"])
    selected=train[0].star_id
    reduced, holdout_again, review_again=run(
        replace(base,excluded_training_star_ids=(selected,)))
    assert selected not in {s.star_id for s in reduced}
    assert {s.star_id for s in holdout}=={
        s.star_id for s in holdout_again}
    assert len(reduced)==len(train)-1
    assert next(item for item in review_again["stars"]
                if item["star_id"]==selected)["role"]=="excluded"


def test_baseline_sky_annulus_has_explicit_star_wing_degeneracy_warning():
    from lsbpipe.photometry.psf_empirical import _star_cutout
    frame=fake_frame()
    mask=np.zeros(frame.shape,dtype=bool)
    src, reason=_star_cutout(
        frame,78.,78.,PSF0Config(),mask)
    assert reason is None
    assert src["sky_qa"]["method"] == "ROBUST_LOCAL_PLANE_ON_STELLAR_ANNULUS"
    assert src["sky_qa"]["wing_sky_degeneracy_warning"] is True
    assert src["sky_qa"]["annulus_inner_in_guessed_fwhm"] == pytest.approx(
        10./4.8)
    assert src["sky_qa"]["sky_uncertainty_covariance_calibrated"] is False
    assert src["sky_qa"]["sky_versus_psf_wings_separable_from_this_cutout"] is False
    assert 0 < src["sky_qa"]["annulus_sigma_clip_retained_fraction"] <= 1


def test_training_outlier_screening_does_not_touch_fixed_holdouts():
    from dataclasses import replace
    from types import SimpleNamespace
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    frame=fake_frame()
    initial=PSF0Config(
        min_train_stars=6,min_holdout_stars=2,
        max_training_stars=30,min_neighbor_separation_pix=32.)
    def run(cfg):
        try:
            result=build_empirical_coadd_psf(
                frame,target_icrs_deg=LEO_P,
                target_protection_arcsec=13.,config=cfg)
            return result.training,result.holdout,result.review_snapshot
        except PSF0QualityError as exc:
            return (
                [SimpleNamespace(**row) for row in exc.diagnostics["training"]],
                [SimpleNamespace(**row) for row in exc.diagnostics["holdout"]],
                exc.review_snapshot)
    train0,holdout0,_=run(initial)
    train1,holdout1,review=run(
        replace(initial,training_outlier_passes=2))
    assert {s.star_id for s in holdout0} == {s.star_id for s in holdout1}
    assert len(train1) >= 6
    assert review["training_residual_screening_history"]
    assert all(h["uses_holdout_to_select"] is False
               for h in review["training_residual_screening_history"])
    assert all(h["coadd_chi_square_calibrated"] is False
               for h in review["training_residual_screening_history"])
    auto=set(review["automatic_training_rejections"])
    assert auto.isdisjoint({s.star_id for s in holdout0})
    assert auto.isdisjoint({s.star_id for s in train1})
    assert len(auto) <= max(1,int(.2*len(train0)))


def test_brightness_rank_guard_preserves_original_training_and_holdout(
        monkeypatch):
    from dataclasses import replace
    import lsbpipe.photometry.psf_empirical as module

    frame=fake_frame()
    cfg=PSF0Config(
        min_train_stars=6,min_holdout_stars=2,
        max_training_stars=30,min_neighbor_separation_pix=32.)
    def synthetic_brightness_coupled_score(entry, kernel, *,
                                           guessed_fwhm_pix):
        amplitude=float(entry[-1]["peak_nMgy"])
        return {
            "star_id":entry[0],
            "score":1. + 0.001*amplitude + 30.*(amplitude > 175.),
            "amplitude_nMgy_diagnostic":amplitude,
            "metric":"SYNTHETIC_BRIGHTNESS_BIAS_NOT_A_PSF_QUALITY_SCORE",
        }
    monkeypatch.setattr(
        module, "training_star_residual", synthetic_brightness_coupled_score)
    initial=module.build_empirical_coadd_psf(
        frame,target_icrs_deg=LEO_P,
        target_protection_arcsec=13.,config=cfg)
    guarded=module.build_empirical_coadd_psf(
        frame,target_icrs_deg=LEO_P,target_protection_arcsec=13.,
        config=replace(cfg,training_outlier_passes=2))
    evidence=guarded.review_snapshot["training_residual_screening_history"]
    assert evidence and evidence[0]["n_above_cutoff"] > 0
    assert evidence[0]["brightness_score_coupling_qa"]["auto_trim_blocked"]
    assert evidence[0]["proposed_before_brightness_guard"]
    assert evidence[0]["selected_for_exclusion"] == []
    assert evidence[0]["n_removed_this_pass"] == 0
    assert evidence[0]["n_proposed_before_brightness_guard"] > 0
    assert guarded.review_snapshot["automatic_training_rejections"] == ()
    assert len(guarded.training) == len(initial.training)
    assert {s.star_id for s in guarded.holdout} == {
        s.star_id for s in initial.holdout}
    assert {s.star_id for s in guarded.training} == {
        s.star_id for s in initial.training}
    assert np.allclose(guarded.kernel,initial.kernel,atol=1e-12)
    assert "AUTO_TRAINING_TRIM_BLOCKED_BY_SCORE_FLUX_COUPLING" in guarded.flags


def test_one_masked_subpixel_near_core_fails_even_if_cutout_mask_fraction_small():
    # Real Leo P mosaic star-0003 had two invalid core pixels
    # but only a tiny masked fraction across its 51x51 cutout.
    from lsbpipe.photometry.psf_empirical import _star_cutout
    baseline = fake_frame()
    ivar = np.array(baseline.invvar_nMgy2, copy=True)
    # Nearest whole central pixel is (78, 78). A hole offset by 2
    # pixels lies IN the PSF fitting core but NOT at the center pixel.
    ivar[78, 80] = 0.
    frame = NativeBandFrame(
        image_nMgy=baseline.image_nMgy,
        invvar_nMgy2=ivar,
        nexp=baseline.nexp,
        maskbits=baseline.maskbits,
        native_wcs=baseline.wcs,
        release=baseline.release,region=baseline.region,
        brick=baseline.brick,band=baseline.band,
        source_evidence={"synthetic":True},
    )
    cfg=PSF0Config(
        cutout_size_pix=51,
        sky_annulus_inner_pix=19.,sky_annulus_outer_pix=24.,
        min_neighbor_separation_pix=55.)
    cutout,reason=_star_cutout(
        frame,78.,78.,cfg,np.zeros(frame.shape,dtype=bool))
    assert cutout is None
    assert reason == "MASKED_STELLAR_PSF_CORE_PIXELS"
    assert (1./51**2) < cfg.max_invalid_fraction
    assert ivar[78,78] > 0


def test_identity_population_diagnostic_never_rejects_or_infers_morphology():
    from lsbpipe.photometry.psf_empirical import _identity_population_qa

    def entry(star_id, *, gaia, peak, width):
        cut = {
            "star_identity": {"gaia_source_id":star_id if gaia else None},
            "shape_qa": {"sigma_eff_pix":width},
            "peak_nMgy":peak,
            "rms_nMgy":.01,
        }
        return (star_id, 0., 0., 0., 0., peak, cut)

    selected = [
        entry(f"gaia-{j}", gaia=True, peak=.2, width=2.2)
        for j in range(3)] + [
        entry(f"tractor-{j}", gaia=False, peak=.2, width=2.8)
        for j in range(3)]
    report = _identity_population_qa(selected)
    assert len(selected) == 6  # no in-place filtering
    assert report["is_selection_gate"] is False
    assert report["populations"]["gaia_dr3"]["n"] == 3
    assert report["populations"]["tractor_psf_without_gaia"]["n"] == 3
    assert report["unmatched_median_width_ratio_tractor_to_gaia"] == (
        pytest.approx(2.8/2.2))
    assert report["n_strata_with_at_least_3_per_population"] == 1
    match = [row for row in report["peak_strata"]
             if row["brightness_matched_comparison_supported"]]
    assert len(match) == 1
    assert match[0]["gaia_median_positive_core_sigma_pix"] == 2.2
    assert match[0]["tractor_only_median_positive_core_sigma_pix"] == 2.8
    assert report["peak_strata"][-1][
        "peak_upper_exclusive_nMgy_per_pix"] is None
    import json
    json.dumps(report, allow_nan=False)

    no_overlap = _identity_population_qa(
        [entry("bright-g", gaia=True, peak=100., width=2.2),
         entry("faint-t", gaia=False, peak=.05, width=3.1)])
    assert no_overlap["n_strata_with_at_least_3_per_population"] == 0
    assert no_overlap["populations"]["gaia_dr3"]["n"] == 1


def test_signed_holdout_coverage_never_reduces_fixed_gaia_denominator():
    from lsbpipe.photometry.psf_holdout_qa import (
        audit_signed_holdout_coverage)

    zones = ("core_r_le_1x_guessed_fwhm",
             "intermediate_1x_to_2x_guessed_fwhm",
             "outer_r_gt_2x_guessed_fwhm")
    def report(star, n=9):
        return {"source_id": star,
                "radial_residuals": {
                    z: {"n_valid_pixels": n,
                        "median_signed_residual_over_peak": .01,
                        "median_absolute_residual_over_peak": .02}
                    for z in zones}}
    full = audit_signed_holdout_coverage(
        ("gaia-a", "gaia-b"), [report("gaia-a"), report("gaia-b")], [])
    assert full["status"] == "COMPLETE"
    assert full["all_fixed_holdouts_and_annuli_scored"] is True
    partial = audit_signed_holdout_coverage(
        ("gaia-a", "gaia-b"), [report("gaia-a")],
        [{"star_id":"gaia-b", "reason":"invalid model amplitude"}])
    assert partial["status"] == "INCOMPLETE_REJECTED"
    assert partial["failed_source_ids"] == ["gaia-b"]
    assert partial["n_expected_holdout_stars"] == 2
    truncated = report("gaia-b")
    truncated["radial_residuals"][zones[2]]["n_valid_pixels"] = 0
    bad = audit_signed_holdout_coverage(
        ("gaia-a", "gaia-b"), [report("gaia-a"), truncated], [])
    assert bad["status"] == "INCOMPLETE_REJECTED"
    assert bad["incomplete_annuli_by_source_id"]["gaia-b"] == [zones[2]]
    with pytest.raises(ValueError, match="Duplicate|foreign"):
        audit_signed_holdout_coverage(
            ("gaia-a", "gaia-b"),
            [report("gaia-a"), report("gaia-a")], [])


def test_legacy_native_holdout_core_uses_actual_subpixel_star_center():
    from types import SimpleNamespace
    from scipy.ndimage import shift
    from lsbpipe.photometry.psf_empirical import _holdout_diagnostic

    yy, xx = np.indices((31,31))
    kernel = np.exp(-((xx-15.)**2+(yy-15.)**2)/9.)
    kernel /= kernel.sum()
    cx, cy = 19.25, 10.5
    shifted = shift(kernel, (cy-15., cx-15.), order=3,
                    mode="constant", cval=0., prefilter=True)
    image = 17.*np.maximum(shifted, 0.)
    data = {
        "center_in_cutout": (cx, cy),
        "image": image,
        "weights": np.ones_like(image),
        "peak_nMgy": float(image.max()),
        "rms_nMgy": .001,
    }
    result = _holdout_diagnostic(SimpleNamespace(star_id="gaia-h"),
                                 data, kernel)
    expected = np.count_nonzero(np.hypot(xx-cx, yy-cy) <= 9.)
    unshifted_count = np.count_nonzero(np.hypot(xx-15., yy-15.) <= 9.)
    assert expected != unshifted_count
    assert result["n_core_aperture_pixels"] == expected
    assert result["core_aperture_center_native_xy"] == [cx, cy]
    assert result["subpixel_center_consistent_with_model_shift"] is True
    assert result["relative_core_abs_residual"] < 1e-9


def test_incomplete_signed_holdout_is_scientific_rejection_not_psf_result(
        monkeypatch):
    import lsbpipe.photometry.psf_empirical as psf
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    def unavailable(**kwargs):
        raise ValueError("Simulated invalid heldout signed amplitude")
    monkeypatch.setattr(psf, "heldout_signed_residual", unavailable)
    with pytest.raises(PSF0QualityError) as err:
        build_empirical_coadd_psf(
            fake_frame(), target_icrs_deg=LEO_P,
            target_protection_arcsec=13.,
            config=PSF0Config(min_neighbor_separation_pix=32.,
                              min_train_stars=6, min_holdout_stars=2,
                              max_training_stars=30))
    qa = err.value.diagnostics
    assert qa["stage"] == "holdout_diagnostic_incomplete"
    assert qa["reason_code"] == "INCOMPLETE_FIXED_HOLDOUT_RESIDUAL_QA"
    cov = qa["signed_holdout_coverage_qa"]
    assert cov["status"] == "INCOMPLETE_REJECTED"
    assert cov["n_expected_holdout_stars"] == qa["n_holdout"]
    assert cov["n_successfully_scored_stars"] == 0
    assert len(qa["signed_holdout_failures"]) == qa["n_holdout"]
    assert qa["status"] == "PSF0_QUALITY_REJECTED_NOT_ADOPTED"
    assert err.value.review_snapshot is not None


def test_implementation_fingerprint_includes_runtime_defaults_and_versions():
    from lsbpipe.photometry.psf_empirical import (
        _implementation_method_fingerprint, PSF0Config)
    cfg = PSF0Config()
    kwargs = {"oversampling": 1, "maxiters": 8,
              "shape": (31,31), "wing_smoothing": True}
    ver = {"photutils": "3.1.1", "astropy": "7.0",
           "numpy": "2.2", "scipy": "1.15"}
    defaults = {"fit_shape_final_if_reported": "(11, 11)",
                "smoothing_kernel_final_if_reported": "auto",
                "convergence_fraction_target_if_reported": 0.95}
    baseline = _implementation_method_fingerprint(
        cfg, kwargs, ver, defaults, "photutils.centroid_symmetry")
    repeated = _implementation_method_fingerprint(
        cfg, dict(reversed(list(kwargs.items()))),
        dict(reversed(list(ver.items()))), defaults,
        "photutils.centroid_symmetry")
    assert baseline == repeated
    assert len(baseline["sha256"]) == 64
    assert _implementation_method_fingerprint(
        cfg, kwargs, {**ver, "photutils":"3.0.1"}, defaults,
        "photutils.centroid_symmetry")["sha256"] != baseline["sha256"]
    assert _implementation_method_fingerprint(
        cfg, {**kwargs,"maxiters":16}, ver, defaults,
        "photutils.centroid_symmetry")["sha256"] != baseline["sha256"]
    assert _implementation_method_fingerprint(
        cfg, kwargs, ver, defaults,
        "photutils.centroid_com")["sha256"] != baseline["sha256"]
    assert _implementation_method_fingerprint(
        cfg, kwargs, ver, {**defaults,"fit_shape_final_if_reported":"(9, 9)"},
        "photutils.centroid_symmetry")["sha256"] != baseline["sha256"]
    json.dumps(baseline, allow_nan=False)


def test_nonconverged_solver_cannot_be_returned_as_successful_preview(
        monkeypatch):
    """Final fraction=1.0 never overrides Photutils converged=False."""
    from types import SimpleNamespace
    import photutils.psf as photutils_psf
    from lsbpipe.photometry.psf_empirical import PSF0QualityError

    original = photutils_psf.EPSFBuilder

    class DeliberatelyNotConverged(original):
        def __call__(self, stars):
            result = super().__call__(stars)
            return SimpleNamespace(
                # Photutils 2.x returns (epsf, fitted_stars), while
                # Photutils 3.1 returns EPSFBuildResults.
                epsf=(result.epsf if hasattr(result, "epsf") else result[0]),
                iterations=getattr(result, "iterations", 8),
                converged=False,
                final_converged_fraction=1.0,
                iteration_info=getattr(result, "iteration_info", None),
                smoothing_kernel=getattr(result, "smoothing_kernel", None),
                fit_shape=getattr(result, "fit_shape", None),
            )

    monkeypatch.setattr(photutils_psf, "EPSFBuilder",
                        DeliberatelyNotConverged)
    with pytest.raises(PSF0QualityError) as failure:
        build_empirical_coadd_psf(
            fake_frame(), target_icrs_deg=LEO_P,
            target_protection_arcsec=13.,
            config=PSF0Config(min_neighbor_separation_pix=32.,
                              min_train_stars=6, min_holdout_stars=2,
                              max_training_stars=30))
    qa = failure.value.diagnostics
    assert qa["stage"] == "epsf_builder_not_converged"
    assert qa["reason_code"] == "EPSF_BUILDER_NOT_CONVERGED"
    assert qa["solver_diagnostics"]["converged_if_reported"] is False
    assert qa["solver_diagnostics"][
        "final_converged_fraction_if_reported"] == 1.0
    assert qa["signed_holdout_coverage_qa"]["status"] == "COMPLETE"
    assert qa["n_holdout"] >= 2
    assert failure.value.review_snapshot is not None
    assert qa["status"] == "PSF0_QUALITY_REJECTED_NOT_ADOPTED"
