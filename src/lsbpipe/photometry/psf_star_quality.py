"""Conservative image-morphology diagnostics for Gaia-confirmed ePSF seeds.

These are explicitly *screening heuristics*, not calibrated star/galaxy
classifiers or PSF shape measurements. Native-cutout signed data are
never altered, and no cuts are applied to the independent holdout
after the training/holdout split.
"""
from __future__ import annotations

import numpy as np


def assess_psf_star_cutout(cutout, *, fwhm_guess_pix):
    image = np.asarray(cutout["image"], dtype=float)
    valid = np.asarray(cutout["valid"], dtype=bool)
    x0, y0 = cutout["center_in_cutout"]
    yy, xx = np.indices(image.shape, dtype=float)
    r = np.hypot(xx-x0, yy-y0)
    # Fixed inner footprint avoids including arbitrary faint wide wings.
    aperture = valid & np.isfinite(image) & (
        r <= min(2.0*float(fwhm_guess_pix), (min(image.shape)-1)/2.-2.))
    positive = np.where(aperture, np.maximum(image, 0.), 0.)
    if np.count_nonzero(positive) < 9 or positive.sum() <= 0:
        return {"accepted":False, "reason":"INSUFFICIENT_STELLAR_CORE",
                "axis_ratio":None, "sigma_eff_pix":None}
    mass = positive.sum()
    cx = float(np.sum(positive*xx)/mass)
    cy = float(np.sum(positive*yy)/mass)
    dx, dy = xx-cx, yy-cy
    mxx = float(np.sum(positive*dx*dx)/mass)
    myy = float(np.sum(positive*dy*dy)/mass)
    mxy = float(np.sum(positive*dx*dy)/mass)
    eigen = np.linalg.eigvalsh([[mxx,mxy],[mxy,myy]])
    if eigen[0] <= 0 or not np.isfinite(eigen).all():
        return {"accepted":False,"reason":"BAD_STELLAR_SHAPE_MOMENTS",
                "axis_ratio":None,"sigma_eff_pix":None}
    ratio = float(np.sqrt(eigen[1]/eigen[0]))
    sigma = float(np.sqrt(np.mean(eigen)))
    center_shift = float(np.hypot(cx-x0,cy-y0))
    reason = None
    if ratio > 1.50:
        reason = "ELONGATED_CUTOUT_NOT_ISOLATED_PSF"
    elif center_shift > max(.75, .3*float(fwhm_guess_pix)):
        reason = "ASYMMETRIC_OR_OFFSET_STELLAR_CORE"
    return {
        "accepted":reason is None,
        "reason":reason,
        "axis_ratio":ratio,
        "sigma_eff_pix":sigma,
        "centroid_shift_pix":center_shift,
        "method":"POSITIVE_NATIVE_CORE_MOMENTS_HEURISTIC",
        "scientifically_calibrated":False,
    }


def assess_sample_widths(entries):
    """Robust field-relative screening; entries are (star_id, metrics).

    A common seeing width is estimated from initially round,
    Gaia-confirmed, valid stellar cores, not DAOStarFinder's guessed FWHM.
    Avoid calling this the empirical PSF FWHM: truncated cutout moments
    are not a measured PSF size or ellipticity calibration.
    """
    sigmas = [
        float(item["sigma_eff_pix"])
        for _, item in entries
        if item["accepted"] and item["sigma_eff_pix"] is not None]
    if len(sigmas) < 5:
        return None
    median = float(np.median(sigmas))
    if not np.isfinite(median) or median <= 0:
        return None
    return {
        "reference_core_sigma_pix":median,
        "min_relative_core_sigma":.65,
        "max_relative_core_sigma":1.45,
        "sample_size":len(sigmas),
        "calibration":"HEURISTIC_UNVALIDATED",
    }
