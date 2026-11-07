"""Independent holdout signed-PSF residual diagnostics are not PSF adoption."""
import numpy as np
import pytest

from lsbpipe.photometry.psf_holdout_qa import (
    heldout_signed_residual, heldout_final_kernel_residual,
    summarize_signed_holdouts, summarize_core_normalized_growth,
    audit_signed_holdout_coverage,
)


def _source():
    yy, xx = np.indices((31,31))
    kernel = np.exp(-.5*((xx-15)**2+(yy-15)**2)/2.**2)
    kernel[15,26] = -.005
    return kernel


def test_signed_model_holdout_uses_core_amplitude_and_does_not_clip_negative():
    kernel = _source()
    original = kernel.tobytes()
    qa = heldout_signed_residual(
        source_id="holdout-1", image=85.*kernel,
        sqrt_ivar=np.ones(kernel.shape), signed_kernel=kernel,
        local_center_xy=(15.,15.), guessed_fwhm_pix=4.8,
        measured_peak=85.)
    assert qa["source_id"] == "holdout-1"
    assert qa["fitted_amplitude_model_units"] == pytest.approx(85.,rel=1e-7)
    assert qa["signed_kernel_unchanged"] is True
    assert qa["correlated_coadd_noise_not_whitened"] is True
    for region in qa["radial_residuals"].values():
        assert region["median_absolute_residual_over_peak"] < 1e-7
    assert kernel.tobytes() == original


def test_outer_residual_is_reported_by_independent_star_sample():
    kernel = _source()
    yy, xx = np.indices(kernel.shape)
    r = np.hypot(xx-15, yy-15)
    measured = 90.*kernel + np.where(r>9.6, .05, 0.)
    qa = heldout_signed_residual(
        source_id="holdout-2", image=measured,
        sqrt_ivar=np.ones(kernel.shape), signed_kernel=kernel,
        local_center_xy=(15.,15.), guessed_fwhm_pix=4.8,
        measured_peak=90.)
    outer = qa["radial_residuals"]["outer_r_gt_2x_guessed_fwhm"]
    inner = qa["radial_residuals"]["core_r_le_1x_guessed_fwhm"]
    assert outer["median_signed_residual_over_peak"] > 0.
    assert outer["median_absolute_residual_over_peak"] > inner[
        "median_absolute_residual_over_peak"]
    another = heldout_signed_residual(
        source_id="holdout-3", image=95.*kernel + np.where(r>9.6, .07, 0.),
        sqrt_ivar=np.ones(kernel.shape), signed_kernel=kernel,
        local_center_xy=(15., 15.), guessed_fwhm_pix=4.8,
        measured_peak=95.)
    summary=summarize_signed_holdouts([qa, another])
    assert summary["n_usable_holdout_stars"] == 2
    assert summary["regions"]["outer_r_gt_2x_guessed_fwhm"]["n_stars"] == 2
    assert summary["regions"]["outer_r_gt_2x_guessed_fwhm"][
        "median_star_abs_residual_over_peak"] > 0.


def test_empty_or_bad_holdout_does_not_fabricate_qa():
    empty = summarize_signed_holdouts([])
    assert empty["n_usable_holdout_stars"] == 0
    assert empty["regions"]["outer_r_gt_2x_guessed_fwhm"][
        "median_star_abs_residual_over_peak"] is None
    kernel = _source()
    with pytest.raises(ValueError, match="Too few"):
        heldout_signed_residual(
            source_id="bad", image=kernel,
            sqrt_ivar=np.zeros(kernel.shape), signed_kernel=kernel,
            local_center_xy=(15.,15.), guessed_fwhm_pix=4.8,
            measured_peak=1.)


def test_repeated_source_id_cannot_create_fake_independent_holdout_evidence():
    kernel = _source()
    qa = heldout_signed_residual(
        source_id="gaia-physical-star-42", image=90.*kernel,
        sqrt_ivar=np.ones(kernel.shape), signed_kernel=kernel,
        local_center_xy=(15., 15.), guessed_fwhm_pix=4.8,
        measured_peak=90.)
    with pytest.raises(ValueError, match="Duplicate holdout source_id"):
        summarize_signed_holdouts([qa, qa])
    with pytest.raises(ValueError, match="Duplicate holdout source_id"):
        summarize_signed_holdouts([qa, {**qa, "source_id": " gaia-physical-star-42 "}])
    with pytest.raises(ValueError, match="nonempty source_id"):
        summarize_signed_holdouts([{**qa, "source_id": "  "}])
    with pytest.raises(TypeError, match="mapping"):
        summarize_signed_holdouts([None])

def test_final_kernel_holdout_uses_identical_radii_after_clipping():
    signed = _source()
    yy, xx = np.indices(signed.shape)
    # A scientifically unphysical signed wing influences the preclip
    # residual. Its delivered model must be checked separately.
    image = 85.*signed
    final = np.maximum(signed, 0.)
    final /= final.sum()
    orig = final.tobytes()
    kwargs = dict(source_id="holdout-fixed", image=image,
                  sqrt_ivar=np.ones(signed.shape),
                  local_center_xy=(15., 15.), guessed_fwhm_pix=4.8,
                  measured_peak=85.)
    signed_qa = heldout_signed_residual(signed_kernel=signed, **kwargs)
    final_qa = heldout_final_kernel_residual(final_kernel=final, **kwargs)
    assert final_qa["kernel_representation"] == "FINAL_NONNEGATIVE_NORMALIZED"
    assert final_qa["final_kernel_nonnegative_and_unit_sum"] is True
    assert "signed_kernel_unchanged" not in final_qa
    assert final.tobytes() == orig
    assert set(signed_qa["radial_residuals"]) == set(final_qa["radial_residuals"])
    for zone in final_qa["radial_residuals"]:
        assert (final_qa["radial_residuals"][zone]["n_valid_pixels"]
                == signed_qa["radial_residuals"][zone]["n_valid_pixels"])
    # Pre-clipping signed residual is zero by construction; the negative
    # wing is absent from the delivered positive PSF.
    assert signed_qa["radial_residuals"]["outer_r_gt_2x_guessed_fwhm"][
        "median_absolute_residual_over_peak"] == pytest.approx(0., abs=1e-12)
    assert final_qa["radial_residuals"]["outer_r_gt_2x_guessed_fwhm"][
        "median_absolute_residual_over_peak"] >= 0.
    coverage = audit_signed_holdout_coverage(["holdout-fixed"],
                                              [final_qa], [])
    assert coverage["status"] == "COMPLETE"


@pytest.mark.parametrize("bad", ["signed", "unnormalized", "nan"])
def test_final_holdout_rejects_nonphysical_kernel(bad):
    kernel = _source()
    if bad == "unnormalized":
        kernel = np.maximum(kernel, 0.)
    elif bad == "nan":
        kernel[0,0] = np.nan
    with pytest.raises(ValueError, match="nonnegative and normalized"):
        heldout_final_kernel_residual(
            source_id="holdout-1", image=np.ones((31,31)),
            sqrt_ivar=np.ones((31,31)), final_kernel=kernel,
            local_center_xy=(15.,15.), guessed_fwhm_pix=4.8,
            measured_peak=1.)


def test_core_normalized_growth_recovers_identical_model_and_cutout():
    kernel = np.maximum(_source(), 0.)
    kernel /= kernel.sum()
    original = kernel.tobytes()
    qa = heldout_final_kernel_residual(
        source_id="gaia-1", image=80.*kernel, sqrt_ivar=np.ones(kernel.shape),
        final_kernel=kernel, local_center_xy=(15., 15.),
        guessed_fwhm_pix=4.8, measured_peak=80.)
    growth = qa["core_normalized_aperture_growth"]
    assert growth["status"] == "DESCRIPTIVE_NOT_SCIENCE_ADOPTED"
    assert growth["core_reference_valid"] is True
    by_radius = {v["radius_in_guessed_fwhm"]: v for v in growth["radii"]}
    for radius in (1., 1.5, 2., 3.):
        row = by_radius[radius]
        assert row["status"] == "FULL_NATIVE_APERTURE"
        assert row["observed_minus_model_growth_over_core"] == pytest.approx(
            0., abs=1e-9)
        assert row["observed_cumulative_flux_over_observed_core"] == pytest.approx(
            row["model_cumulative_flux_over_model_core"], abs=1e-9)
    assert by_radius[1.]["observed_cumulative_flux_over_observed_core"] == pytest.approx(1.)
    # At 4 FWHM the 31x31 stamp cannot contain a full radius-19.2 disk.
    assert by_radius[4.]["status"] == "NOT_FULLY_CONTAINED"
    assert by_radius[4.]["observed_minus_model_growth_over_core"] is None
    assert kernel.tobytes() == original


def test_core_normalized_growth_exposes_wing_excess_hidden_by_pixel_medians():
    kernel = np.maximum(_source(), 0.)
    kernel /= kernel.sum()
    yy, xx = np.indices(kernel.shape)
    r = np.hypot(xx-15., yy-15.)
    # Add only annular signal, outside the fitted core but within 2 FWHM.
    star = 80.*kernel + np.where((r > 5.) & (r < 9.), 0.05, 0.)
    qa = heldout_final_kernel_residual(
        source_id="gaia-2", image=star, sqrt_ivar=np.ones(kernel.shape),
        final_kernel=kernel, local_center_xy=(15., 15.),
        guessed_fwhm_pix=4.8, measured_peak=80.)
    profile = {v["radius_in_guessed_fwhm"]: v for v in
               qa["core_normalized_aperture_growth"]["radii"]}
    assert profile[1.]["observed_minus_model_growth_over_core"] == pytest.approx(0., abs=1e-9)
    assert profile[2.]["observed_minus_model_growth_over_core"] > 0.01
    summary = summarize_core_normalized_growth([qa])
    r2 = next(v for v in summary["radii"] if v["radius_in_guessed_fwhm"] == 2.)
    assert r2["n_full_aperture_holdouts"] == 1
    assert r2["median_observed_minus_model_growth_over_core"] > 0.01
    assert summary["no_threshold_or_science_adoption_implication"] is True


def test_growth_summary_excludes_masked_and_truncated_apertures():
    kernel = np.maximum(_source(), 0.)
    kernel /= kernel.sum()
    weights = np.ones(kernel.shape)
    weights[15, 22] = 0.
    qa = heldout_final_kernel_residual(
        source_id="gaia-masked", image=80.*kernel,
        sqrt_ivar=weights, final_kernel=kernel,
        local_center_xy=(15.,15.), guessed_fwhm_pix=4.8,
        measured_peak=80.)
    growth = {v["radius_in_guessed_fwhm"]: v for v in
              qa["core_normalized_aperture_growth"]["radii"]}
    assert growth[1.]["status"] == "FULL_NATIVE_APERTURE"
    assert growth[2.]["status"] == "MASKED_APERTURE_DIAGNOSTIC_ONLY"
    assert growth[2.]["n_valid_native_pixels"] < growth[2.]["n_disk_native_pixels"]
    summary = summarize_core_normalized_growth([qa])
    by_radius = {v["radius_in_guessed_fwhm"]: v for v in summary["radii"]}
    assert by_radius[1.]["n_full_aperture_holdouts"] == 1
    assert by_radius[2.]["n_full_aperture_holdouts"] == 0
    assert by_radius[2.]["median_observed_minus_model_growth_over_core"] is None
    assert by_radius[4.]["n_full_aperture_holdouts"] == 0


def test_growth_signed_and_delivered_use_same_native_aperture_geometry():
    signed = _source()
    final = np.maximum(signed, 0.)
    final /= final.sum()
    kwargs = dict(source_id="gaia-fixed", image=85.*signed,
                  sqrt_ivar=np.ones(signed.shape),
                  local_center_xy=(15., 15.),
                  guessed_fwhm_pix=4.8, measured_peak=85.)
    before = heldout_signed_residual(signed_kernel=signed, **kwargs)
    after = heldout_final_kernel_residual(final_kernel=final, **kwargs)
    for a,b in zip(before["core_normalized_aperture_growth"]["radii"],
                   after["core_normalized_aperture_growth"]["radii"]):
        assert a["radius_in_guessed_fwhm"] == b["radius_in_guessed_fwhm"]
        assert a["status"] == b["status"]
        assert a["n_valid_native_pixels"] == b["n_valid_native_pixels"]
    with pytest.raises(ValueError, match="Duplicate holdout"):
        summarize_core_normalized_growth([before, before])
