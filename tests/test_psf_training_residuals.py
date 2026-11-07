"""Experimental star-level rejection is independent of heldout stars."""
import numpy as np
import pytest

from lsbpipe.photometry.psf_training_residuals import (
    training_star_residual, choose_training_outliers,
)


def _trial(star_id, *, neighbor_amp=0., local_noise=.2):
    yy, xx = np.indices((51, 51), dtype=float)
    x0, y0 = 25.2, 24.8
    # PSF model is centered on its *own* native integer grid; the
    # synthetic star image is shifted to the measured fractional center
    # using the SAME interpolation as the evaluation path.
    from scipy.ndimage import shift
    model = np.exp(-.5*((xx-25.)**2+(yy-25.)**2)/(2.1**2))
    image = 100.*shift(
        model, (y0-25., x0-25.), order=3,
        mode="constant", cval=0., prefilter=True)
    if neighbor_amp:
        image = image + neighbor_amp*np.exp(
            -.5*((xx-34.)**2+(yy-31.)**2)/(1.6**2))
    cutout = {
        "image":image,
        "weights":np.full(image.shape,1./local_noise),
        "center_in_cutout":(x0,y0),
        "rms_nMgy":local_noise,
    }
    entry=(star_id,12.,15.,155.4,18.1,100.,cutout)
    return entry, model


def test_psfex_style_score_rejects_neighbor_contamination_without_holdout():
    clean,model=_trial("star-0001")
    contaminated,_=_trial("star-0015",neighbor_amp=35.)
    good=training_star_residual(clean,model,guessed_fwhm_pix=4.8)
    bad=training_star_residual(contaminated,model,guessed_fwhm_pix=4.8)
    assert good["score"] < 0.01
    assert bad["score"] > 3.
    sample=[]
    for j in range(14):
        entry,_=_trial(f"star-{j+1:04d}",neighbor_amp=0.)
        sample.append(training_star_residual(
            entry,model,guessed_fwhm_pix=4.8))
    sample.append(bad)
    rejected, evidence=choose_training_outliers(sample,min_train=10)
    assert rejected==("star-0015",)
    assert evidence["n_above_cutoff"]==1
    assert evidence["metric"]=="TRAIN_ONLY_PROVISIONAL_NONCALIBRATED"


def test_trimming_is_bounded_even_if_many_stars_are_bad():
    good=[{"star_id":f"star-{j:04d}","score":1.+.03*(j%5)}
          for j in range(1,19)]
    bad=[{"star_id":f"star-{j:04d}","score":30.+j}
         for j in range(19,24)]
    selected, summary=choose_training_outliers(
        good+bad,min_train=10,max_remove_fraction=.12)
    assert len(selected) <= int(.12*len(good+bad))
    assert summary["n_above_cutoff"] >= len(selected)
    assert summary["n_removed_this_pass"] == len(selected)


def test_unvalidated_train_residuals_do_not_claim_chi_square():
    entry,model=_trial("star-0001",neighbor_amp=8.)
    metric=training_star_residual(entry,model,guessed_fwhm_pix=4.8)
    assert "NOT_CHI_SQUARE" in metric["metric"]
    with pytest.raises(ValueError,match="pixels"):
        training_star_residual(
            ("star-0001",None,None,None,None,None,{
                **entry[-1],"weights":np.zeros((51,51)),
            }),model,guessed_fwhm_pix=4.8)


def test_brightness_coupling_audit_blocks_highly_flux_ordered_outliers():
    from lsbpipe.photometry.psf_training_residuals import (
        brightness_bias_audit)
    # Reproduces the *failure mode* observed in the real Leo P QA:
    # residual-score systematically increases with PSF amplitude.
    scores=[{
        "star_id":f"star-{i+1:04d}",
        "amplitude_nMgy_diagnostic":float(i+1),
        "score":float(i+1)/6. + 0.03*(i%3),
    } for i in range(40)]
    result=brightness_bias_audit(scores,("star-0039","star-0040"))
    assert result["score_amplitude_spearman_rho"] > .99
    assert result["auto_trim_blocked"] is True
    assert result["status"] == "BRIGHTNESS_ASSOCIATION_BLOCKS_AUTO_TRIM"
    assert result["median_amplitude_proposed_remove_nMgy"] > (
        result["median_amplitude_remaining_nMgy"])
    assert result["scores_use_training_stars_in_their_own_ePSF"] is True


def test_neighbor_profile_outlier_is_not_dismissed_due_to_brightness():
    from lsbpipe.photometry.psf_training_residuals import (
        brightness_bias_audit)
    # At constant model amplitude, rank correlation is uninformative;
    # a real profile outlier may still be provisionally removed.
    scores=[{
        "star_id":f"star-{i+1:04d}",
        "amplitude_nMgy_diagnostic":10.,
        "score":9. if i==11 else 1.+.04*(i%5),
    } for i in range(20)]
    result=brightness_bias_audit(scores,("star-0012",))
    assert result["auto_trim_blocked"] is False
    assert result["score_amplitude_spearman_rho"] is None


def test_brightness_coupling_guard_requires_positive_finite_amplitudes():
    from lsbpipe.photometry.psf_training_residuals import (
        brightness_bias_audit)
    with pytest.raises(ValueError,match="amplitude"):
        brightness_bias_audit([{"star_id":"star-0001",
                                "amplitude_nMgy_diagnostic":-1.,
                                "score":3.}])


def test_unscorable_star_fails_closed_without_invented_flux_amplitude():
    from lsbpipe.photometry.psf_training_residuals import (
        brightness_bias_audit)
    rows=[{
        "star_id":f"star-{i+1:04d}",
        "amplitude_nMgy_diagnostic":float(i+1),
        "score":1.+.01*i,
    } for i in range(20)]
    rows[3]={
        "star_id":"star-0004","score":1e6,
        "error":"Insufficient pixels for PSF fit",
        "metric":"UNSCORABLE_TRAIN_STAR",
    }
    evidence=brightness_bias_audit(rows,("star-0004",))
    assert evidence["auto_trim_blocked"] is True
    assert evidence["unscorable_star_ids"] == ["star-0004"]
    assert evidence["score_amplitude_spearman_rho"] is None
