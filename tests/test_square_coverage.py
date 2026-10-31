"""WCS-exact square QA: finite pixels, masks, NEXP and brick geometry."""
import numpy as np
import pytest

pytest.importorskip("astropy")
from astropy.wcs import WCS

from lsbpipe.surveys.legacy import SkyField
from lsbpipe.surveys.square_coverage import square_coverage_from_arrays


def sample(*, center_ra=40.096208, center_dec=1.22925, shape=(200, 200)):
    w = WCS(naxis=2)
    w.wcs.crpix = [(shape[1]+1)/2, (shape[0]+1)/2]
    w.wcs.cdelt = [-1/3600, 1/3600]
    w.wcs.crval = [center_ra,center_dec]
    w.wcs.ctype = ["RA---TAN","DEC--TAN"]
    im = np.ones(shape, dtype="f4")
    ivar = np.full(shape, 5., dtype="f4")
    nexp = np.full(shape, 8, dtype=np.int16)
    bits = np.zeros(shape, dtype=np.int16)
    return w, im, ivar, nexp, bits


def inspect(w, im, ivar, nexp, bits, *, field=None, chunk_rows=128):
    if field is None:
        field = SkyField(40.096208, 1.22925, 1.)
    return square_coverage_from_arrays(
        im, ivar, nexp, bits, w, field=field, band="r",
        chunk_rows=chunk_rows)


def test_complete_square_keeps_sga_galaxy_and_separates_medium_gaia():
    w, im, ivar, nexp, bits = sample()
    bits[100, 100] = 1 << 12
    bits[104, 104] = 1 << 11
    result = inspect(w,im,ivar,nexp,bits)
    assert result.status == "DIAGNOSTIC_NOT_ADOPTED"
    assert result.polygon_within_single_brick
    assert result.approximate_geometric_fraction > 0.99
    assert 3500 <= result.pixel_centers_sampled <= 3700
    assert result.instrumentally_usable_fraction == 1.
    assert result.maskbit_counts["GALAXY"] == 1
    assert result.maskbit_counts["MEDIUM"] == 1
    assert result.maskbit_counts["SATUR_R"] == 0
    assert result.maskbit_fractions["GALAXY"] == pytest.approx(
        1 / result.pixel_centers_sampled)
    assert result.nexp_percentiles_positive["median"] == 8
    assert "SQUARE_GAIA_MEDIUM_STAR_WARNING" in result.warnings
    assert "SQUARE_SGA_GALAXY_BIT_PRESENT_DO_NOT_AUTOMASK" in result.warnings


def test_hard_bad_pixel_and_nexp_and_invvar_fail_without_changing_warnings():
    w, im, ivar, nexp, bits = sample()
    bits[100,100] = (1 << 12) | (1 << 3)
    bits[101,101] = 1 << 6
    nexp[99,99] = 0
    ivar[98,98] = 0
    result = inspect(w,im,ivar,nexp,bits)
    assert result.positive_nexp_fraction < 1
    assert result.positive_ivar_fraction < 1
    assert result.instrumentally_usable_pixels == result.pixel_centers_sampled-4
    assert result.maskbit_counts["SATUR_R"] == 1
    assert result.maskbit_counts["ALLMASK_R"] == 1
    assert result.maskbit_counts["GALAXY"] == 1
    assert "SQUARE_INVALID_OR_INSTRUMENTALLY_MASKED_PIXELS" in result.warnings


def test_partly_outside_brick_is_measured_but_never_accepted():
    w, im, ivar, nexp, bits = sample()
    ra, dec = w.all_pix2world(5., 5., 0)
    field = SkyField(float(ra), float(dec), 1.)
    result = inspect(w,im,ivar,nexp,bits,field=field)
    assert not result.polygon_within_single_brick
    assert 0 < result.approximate_geometric_fraction < .8
    assert "SQUARE_FOOTPRINT_EXTENDS_OUTSIDE_BRICK" in result.warnings
    assert result.pixel_centers_sampled > 0
    # Even when the visible pixels are valid, missing sky is not valid data.
    assert result.instrumentally_usable_fraction == 1.


def test_chunked_exact_sky_footprint_matches_single_chunk_near_ra_wrap():
    w, im, ivar, nexp, bits = sample(center_ra=359.999,center_dec=70.)
    field = SkyField(359.999,70.,1.)
    first = inspect(w,im,ivar,nexp,bits,field=field,chunk_rows=1)
    second = inspect(w,im,ivar,nexp,bits,field=field,chunk_rows=128)
    assert first.polygon_within_single_brick
    assert first.pixel_centers_sampled == second.pixel_centers_sampled
    assert first.maskbit_counts == second.maskbit_counts
    assert first.nexp_percentiles_positive == second.nexp_percentiles_positive
    assert first.mean_nexp_where_positive == second.mean_nexp_where_positive


def test_bad_shape_mask_dtype_and_invalid_chunk_rejected():
    w, im, ivar, nexp, bits = sample()
    with pytest.raises(ValueError, match="common nonempty 2D"):
        inspect(w,im,ivar[:-5],nexp,bits)
    with pytest.raises(ValueError, match="16-bit or 32-bit"):
        inspect(w,im,ivar,nexp,bits.astype("f4"))
    with pytest.raises(ValueError, match="chunk_rows"):
        inspect(w,im,ivar,nexp,bits,chunk_rows=0)


def test_all_zero_exposures_are_not_rated_as_covered():
    w, im, ivar, nexp, bits = sample()
    nexp[:] = 0
    result = inspect(w,im,ivar,nexp,bits)
    assert result.positive_nexp_fraction == 0.
    assert result.instrumentally_usable_fraction == 0.
    assert result.mean_nexp_where_positive is None
    assert result.nexp_percentiles_positive is None
    assert "SQUARE_INVALID_OR_INSTRUMENTALLY_MASKED_PIXELS" in result.warnings
