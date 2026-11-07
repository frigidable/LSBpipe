"""Core/intermediate/outer negative ePSF diagnostics are explicitly descriptive."""
import numpy as np
import pytest

from lsbpipe.photometry.psf_signed_qa import signed_psf_qa


def test_signed_mass_split_reproduces_global_fraction_without_clipping():
    image = np.zeros((25, 25), float)
    image[12, 12] = 10.
    image[12, 13] = -1.
    image[12, 16] = -2.
    image[12, 21] = -3.
    original = image.tobytes()
    analysis = signed_psf_qa(image, fwhm_guess_pix=3.)
    assert analysis["negative_absolute_mass_fraction"] == pytest.approx(6/16)
    assert analysis["negative_absolute_mass"] == pytest.approx(6)
    assert analysis["signed_total_mass"] == pytest.approx(4.)
    assert analysis["peak_offset_from_grid_center_pix"] == pytest.approx(0.)
    zones = analysis["annuli"]
    assert zones["core_r_le_1x_guessed_fwhm"]["negative_absolute_mass"] == pytest.approx(1.)
    assert zones["intermediate_1x_to_2x_guessed_fwhm"]["negative_absolute_mass"] == pytest.approx(2.)
    assert zones["outer_r_gt_2x_guessed_fwhm"]["negative_absolute_mass"] == pytest.approx(3.)
    total_fraction = sum(z["negative_fraction_of_total_absolute_mass"]
                         for z in zones.values())
    assert total_fraction == pytest.approx(analysis["negative_absolute_mass_fraction"])
    assert image.tobytes() == original
    assert analysis["science_state"] == "DIAGNOSTIC_SIGNED_PSF_NOT_ADOPTED"
    assert analysis["guessed_fwhm_pix_not_measured"] == 3.


def test_signed_mass_diagnostic_is_safely_defined_for_zero_negative_mass():
    image = np.zeros((21, 21))
    image[10, 10] = 1.
    result = signed_psf_qa(image, 3.)
    assert result["negative_absolute_mass_fraction"] == 0.
    assert all(x["negative_fraction_of_total_absolute_mass"] == 0.
               for x in result["annuli"].values())


@pytest.mark.parametrize("bad", [
    np.zeros((21, 21)),
    np.ones((10, 10)),
    np.full((21, 21), np.nan),
    np.array([1., 2.]),
])
def test_invalid_signed_kernel_rejected(bad):
    with pytest.raises(ValueError):
        signed_psf_qa(bad, 3.)


@pytest.mark.parametrize("width", [0, -1, float("nan")])
def test_invalid_guessed_width_is_rejected(width):
    with pytest.raises(ValueError):
        signed_psf_qa(np.ones((21, 21)), width)
