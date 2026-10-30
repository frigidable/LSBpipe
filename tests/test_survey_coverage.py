"""Offline synthetic FITS tests for release brick coverage: no internet calls."""
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip("astropy")
from astropy.io import fits
from astropy.wcs import WCS

from lsbpipe.surveys.coverage import (
    coverage_from_arrays, inspect_local_brick, save_coverage)
from lsbpipe.surveys.legacy import BrickHit, product_for


def fixture_arrays(shape=(72, 76)):
    w = WCS(naxis=2)
    w.wcs.crpix = [(shape[1]+1)/2, (shape[0]+1)/2]
    w.wcs.cdelt = [-1./3600, 1./3600]
    w.wcs.crval = [154.77, 46.45]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    a = np.full(shape, 0.03)
    iv = np.ones(shape)
    nexp = np.full(shape, 3, dtype=np.int16)
    bits = np.zeros(shape, dtype=np.int16)
    return w, a, iv, nexp, bits


def args():
    return dict(ra_deg=154.77, dec_deg=46.45, band="r",
                target_radius_arcsec=8., sky_inner_arcsec=12.,
                sky_outer_arcsec=20.)


def test_complete_coverage_has_separate_target_and_sky_metrics():
    w, a, iv, n, bits = fixture_arrays()
    report = coverage_from_arrays(a, iv, n, bits, w, **args())
    assert report.status == "DIAGNOSTIC_NOT_ADOPTED"
    assert tuple(x.label for x in report.regions) == ("target", "sky_annulus")
    for item in report.regions:
        assert item.pixel_centers_sampled > 100
        assert item.usable_fraction == 1
        assert item.positive_ivar_fraction == 1
        assert item.positive_nexp_fraction == 1
        assert item.mean_nexp_where_positive == 3
        assert item.estimated_footprint_fraction > .95


def test_nexp_ivars_and_instrumental_flags_are_not_interchangeable():
    w, a, iv, nexp, bits = fixture_arrays()
    center = (36, 38)
    bits[center] |= 1 << 1  # BRIGHT warning, but not forced exclusion
    report = coverage_from_arrays(a, iv, nexp, bits, w, **args())
    assert report.regions[0].usable_fraction == 1.
    assert report.regions[0].bright_warning_fraction > 0.
    nexp[center] = 0
    report = coverage_from_arrays(a, iv, nexp, bits, w, **args())
    assert report.regions[0].usable_fraction < 1.
    assert report.regions[0].positive_nexp_fraction < 1.
    nexp[center] = 3
    bits[center] |= 1 << 3  # SATUR_R: conservative hard exclusion
    report = coverage_from_arrays(a, iv, nexp, bits, w, **args())
    assert report.regions[0].usable_fraction < 1.
    assert report.regions[0].positive_nexp_fraction == 1.
    bits[center] = 0
    iv[center] = 0
    report = coverage_from_arrays(a, iv, nexp, bits, w, **args())
    assert report.regions[0].positive_ivar_fraction < 1.
    assert report.regions[0].usable_fraction < 1.


def test_field_near_edge_is_not_reported_fully_covered():
    w, a, iv, nexp, bits = fixture_arrays()
    ra, dec = w.all_pix2world(3., 3., 0)
    p = args()
    p['ra_deg'],p['dec_deg'] = ra,dec
    p['target_radius_arcsec'] = 15.
    p['sky_inner_arcsec'] = 18.
    p['sky_outer_arcsec'] = 26.
    report = coverage_from_arrays(a, iv, nexp, bits, w, **p)
    assert report.regions[0].estimated_footprint_fraction < .95
    assert 'TARGET_APPROX_FOOTPRINT_INCOMPLETE' in report.warnings


def test_invalid_radii_and_grids_rejected():
    w, a, iv, nexp, bits = fixture_arrays()
    with pytest.raises(ValueError, match='Radii'):
        coverage_from_arrays(a, iv, nexp, bits, w, **{
            **args(), 'target_radius_arcsec': 12., 'sky_inner_arcsec': 9.})
    with pytest.raises(ValueError, match='same 2D grid'):
        coverage_from_arrays(a, iv, nexp[:60], bits, w, **args())
    with pytest.raises(ValueError, match='MASKBITS'):
        coverage_from_arrays(a, iv, nexp, bits.astype(float), w, **args())


def write_brick(tmp_path, *, shifted_nexp=False):
    w, a, iv, nexp, bits = fixture_arrays()
    brick = BrickHit("dr9", "north", "1544p465", 154., 155., 46., 47.,
                     {"r":3})
    root = tmp_path
    science = {"image":a, "invvar":iv, "nexp":nexp, "maskbits":bits}
    for kind, values in science.items():
        entry = product_for(brick, kind, None if kind == "maskbits" else "r")
        path = root / entry.relative_path
        path.parent.mkdir(parents=True,exist_ok=True)
        header = w.to_header()
        if kind == 'nexp' and shifted_nexp:
            header['CRVAL1'] += .0005
        if kind == "maskbits":
            fits.HDUList([fits.PrimaryHDU(),
                          fits.ImageHDU(data=values,header=header)]
                        ).writeto(path)
        else:
            fits.PrimaryHDU(data=values,header=header).writeto(path)
    return root


def test_original_fits_hdu1_and_provenance(tmp_path):
    root = write_brick(tmp_path)
    report = inspect_local_brick(
        root, release='dr9',region='north',brick='1544p465',
        band='r',**{k:v for k,v in args().items() if k != 'band'})
    assert report.regions[0].usable_fraction == 1.
    assert report.files['maskbits'].endswith('maskbits.fits.fz')
    p = save_coverage(report,tmp_path/'coverage.json')
    assert '"DIAGNOSTIC_NOT_ADOPTED"' in p.read_text('utf-8')


def test_bad_nexp_wcs_fails_closed(tmp_path):
    root = write_brick(tmp_path,shifted_nexp=True)
    with pytest.raises(ValueError,match='nexp WCS grid mismatch'):
        inspect_local_brick(
            root,release='dr9',region='north',brick='1544p465',
            band='r',**{k:v for k,v in args().items() if k != 'band'})


def test_missing_science_layer_rejected(tmp_path):
    with pytest.raises(FileNotFoundError,match='Missing required science FITS'):
        inspect_local_brick(
            tmp_path,release='dr9',region='north',brick='1544p465',
            band='r',**{k:v for k,v in args().items() if k != 'band'})
