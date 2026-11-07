"""Version-independent diagnostic anatomy of a signed empirical PSF.

The ePSF solver can return negative pixels from sky residuals, interpolation,
miscentering, or fitting noise. A single global negative fraction does not
tell a researcher where those terms occur. This module characterizes the
*unmodified signed model*. It never clips, adopts, exports an accepted PSF,
or converts a diagnostic into an astrophysical significance measure.
"""
from __future__ import annotations

import math
import numpy as np


def signed_psf_qa(kernel: np.ndarray, fwhm_guess_pix: float) -> dict:
    """Compute bounded, JSON-safe radial signed-mass diagnostics.

    Annular divisions use the *input detection-width guess*, NOT a measured
    PSF FWHM. Fractions use the absolute mass of all signed pixels as a
    common denominator, so component contributions add to the global
    negative fraction. PSF radial profiles are not encircled-energy
    calibration, sky subtraction, or an SBF PSF qualification.
    """
    data = np.asarray(kernel, dtype=float)
    if (data.ndim != 2 or not all(n % 2 for n in data.shape)
            or min(data.shape) < 9 or not np.all(np.isfinite(data))
            or not np.any(data != 0)):
        raise ValueError("Signed ePSF diagnostic requires finite nonzero odd-grid data")
    if not isinstance(fwhm_guess_pix, (float, int)) or (
            not math.isfinite(fwhm_guess_pix) or fwhm_guess_pix <= 0):
        raise ValueError("A finite positive guessed detection FWHM is required")
    y, x = np.indices(data.shape)
    cx, cy = (data.shape[1]-1)/2, (data.shape[0]-1)/2
    r = np.hypot(x-cx, y-cy)
    abs_total = float(np.abs(data).sum())
    negative = np.maximum(-data, 0.)
    positive = np.maximum(data, 0.)
    b1, b2 = float(fwhm_guess_pix), 2*float(fwhm_guess_pix)
    regions = {
        "core_r_le_1x_guessed_fwhm": r <= b1,
        "intermediate_1x_to_2x_guessed_fwhm": (r > b1) & (r <= b2),
        "outer_r_gt_2x_guessed_fwhm": r > b2,
    }
    annuli = {}
    for name, mask in regions.items():
        this_abs = float(np.abs(data[mask]).sum())
        this_negative = float(negative[mask].sum())
        annuli[name] = {
            "n_pixels": int(mask.sum()),
            "signed_sum": float(data[mask].sum()),
            "negative_absolute_mass": this_negative,
            "negative_fraction_of_total_absolute_mass": this_negative/abs_total,
            "negative_fraction_within_annulus": (
                this_negative/this_abs if this_abs > 0 else 0.),
            "positive_absolute_mass": float(positive[mask].sum()),
        }
    peak_y, peak_x = np.unravel_index(int(np.argmax(data)), data.shape)
    return {
        "science_state": "DIAGNOSTIC_SIGNED_PSF_NOT_ADOPTED",
        "guessed_fwhm_pix_not_measured": float(fwhm_guess_pix),
        "radial_boundaries_in_guessed_fwhm": [1., 2.],
        "negative_absolute_mass_fraction": float(negative.sum()/abs_total),
        "negative_absolute_mass": float(negative.sum()),
        "positive_absolute_mass": float(positive.sum()),
        "signed_total_mass": float(data.sum()),
        "absolute_total_mass": abs_total,
        "most_negative_pixel_value": float(data.min()),
        "positive_peak_pixel_value": float(data.max()),
        "peak_pixel_xy_0_based": [int(peak_x), int(peak_y)],
        "peak_offset_from_grid_center_pix": float(
            np.hypot(peak_x-cx, peak_y-cy)),
        "annuli": annuli,
        "interpretation": (
            "These are uncalibrated signed ePSF diagnostics, not an "
            "empirical flux-leakage rate, negative physical PSF probability, "
            "measured FWHM or scientific acceptance criterion."),
    }
