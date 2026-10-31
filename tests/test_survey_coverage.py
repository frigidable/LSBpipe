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
    with pytest.raises(ValueError, match='Require 0'):
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


def test_negative_32bit_maskbits_and_8bit_maskbits_fail_closed():
    w, a, iv, nexp, bits = fixture_arrays()
    with pytest.raises(ValueError, match='MASKBITS'):
        coverage_from_arrays(a, iv, nexp, bits.astype(np.uint8), w, **args())
    negative = bits.astype(np.int32)
    negative[0,0] = -1
    with pytest.raises(ValueError, match='Negative signed-32'):
        coverage_from_arrays(a, iv, nexp, negative, w, **args())


def test_realistic_fpacked_compressed_extension_and_hdu_provenance(tmp_path):
    """Tile-compressed coadds may have empty primary + one 2D image HDU."""
    w, image, invvar, nexp, bits = fixture_arrays()
    brick = BrickHit("dr9","south","1544p465",154.,155.,46.,47.,
                     {"r":9})
    inputs = {"image":image,"invvar":invvar,"nexp":nexp,
              "maskbits":bits.astype(np.int16)}
    for kind, values in inputs.items():
        rel = product_for(brick, kind, None if kind == "maskbits" else "r")
        path = tmp_path / rel.relative_path
        path.parent.mkdir(parents=True,exist_ok=True)
        hdus = fits.HDUList([
            fits.PrimaryHDU(),
            fits.CompImageHDU(data=values,header=w.to_header())])
        hdus.writeto(path)
    result = inspect_local_brick(
        tmp_path,release="dr9",region="south",brick="1544p465",
        band="r",**{k:v for k,v in args().items() if k!="band"})
    assert result.regions[0].usable_fraction == 1
    assert result.hdu_indices == {"image":1,"invvar":1,"nexp":1,"maskbits":1}
    assert result.files["image"].endswith("image-r.fits.fz")
    assert result.status == "DIAGNOSTIC_NOT_ADOPTED"


def test_refuse_ambiguous_multiple_compressed_image_extensions(tmp_path):
    from lsbpipe.surveys.coverage import _science_hdu_index
    w, image, *_ = fixture_arrays()
    with fits.HDUList([
            fits.PrimaryHDU(), fits.ImageHDU(data=image,header=w.to_header()),
            fits.ImageHDU(data=image,header=w.to_header())]) as hdul:
        with pytest.raises(ValueError,match="unambiguous"):
            _science_hdu_index(hdul, "image")
        assert _science_hdu_index(hdul, "maskbits") == 1


def test_optical_mask_must_use_hdu1_not_wise_extension():
    from lsbpipe.surveys.coverage import _science_hdu_index
    w,image,*_ = fixture_arrays()
    with fits.HDUList([
            fits.PrimaryHDU(), fits.ImageHDU(),
            fits.ImageHDU(data=image, header=w.to_header())]) as hdul:
        with pytest.raises(ValueError,match="Optical MASKBITS HDU1"):
            _science_hdu_index(hdul, "maskbits")



def test_sga_galaxy_bit_retained_as_target_signal_with_audited_counts():
    """SGA footprint is not a bad-pixel flag: don't lose the galaxy light."""
    w, image, ivar, nexp, maskbits = fixture_arrays()
    target_px = (35, 37)  # within the target circle
    sky_px = (20, 37)     # within the sky ring (12–20 arcsec)
    maskbits[target_px] |= (1 << 12)  # SGA GALAXY
    maskbits[sky_px] |= (1 << 11)    # MEDIUM bright star warning
    maskbits[sky_px] |= (1 << 0)     # outside BRICK_PRIMARY
    report = coverage_from_arrays(
        image, ivar, nexp, maskbits, w, **args())
    target, sky = report.regions
    assert target.usable_fraction == 1.
    assert target.maskbit_counts["GALAXY"] == 1
    assert target.maskbit_fractions["GALAXY"] == pytest.approx(
        1 / target.pixel_centers_sampled)
    assert target.galaxy_warning_fraction == pytest.approx(
        target.maskbit_fractions["GALAXY"])
    assert target.maskbit_counts["BRIGHT"] == 0
    assert "TARGET_SGA_GALAXY_BIT_PRESENT_DO_NOT_AUTOMASK" in report.warnings
    assert sky.usable_fraction == 1.
    assert sky.maskbit_counts["MEDIUM"] == 1
    assert sky.maskbit_counts["NPRIMARY"] == 1
    assert "SKY_ANNULUS_MEDIUM_WARNING" in report.warnings
    assert "SKY_ANNULUS_NPRIMARY_WARNING" in report.warnings
    assert sky.maskbit_counts["GALAXY"] == 0
    for region in report.regions:
        assert set(region.maskbit_counts) == {
            "NPRIMARY", "BRIGHT", "MEDIUM", "GALAXY", "CLUSTER",
            "BAILOUT", "SATUR_R", "ALLMASK_R"}
        assert all(isinstance(v, int) for v in region.maskbit_counts.values())


def test_band_specific_saturated_and_allmask_bits_counted_separately():
    w, image, ivar, nexp, flags = fixture_arrays()
    center = (35, 37)
    flags[center] |= (1 << 3)   # SATUR_R
    flags[center] |= (1 << 6)   # ALLMASK_R
    flags[center] |= (1 << 12)  # SGA: *not* additional invalidity
    report = coverage_from_arrays(
        image, ivar, nexp, flags, w, **args())
    target = report.regions[0]
    assert target.maskbit_counts["SATUR_R"] == 1
    assert target.maskbit_counts["ALLMASK_R"] == 1
    assert target.maskbit_counts["GALAXY"] == 1
    assert target.usable_pixels == target.pixel_centers_sampled - 1
    assert "TARGET_INVALID_OR_MASKED_PIXELS" in report.warnings
    assert target.usable_fraction == pytest.approx(
        1 - 1 / target.pixel_centers_sampled)
