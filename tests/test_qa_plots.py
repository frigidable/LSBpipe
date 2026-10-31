"""Single-brick real-FITS visualization: offline, provenance-preserving tests."""
import numpy as np
import pytest

from lsbpipe.surveys.qa_plots import classify_maskbits, MASK_LABELS


def test_sga_is_a_diagnostic_category_not_an_automatic_rejection():
    bits = np.array([
        [0, 1 << 12, 1 << 1, (1 << 12) | (1 << 11)],
        [1 << 3, 1 << 6, (1 << 12) | (1 << 3), 1 << 0],
    ], dtype=np.int32)
    actual = classify_maskbits(bits, "r")
    np.testing.assert_array_equal(
        actual, [[0, 1, 2, 2], [3, 3, 3, 2]])
    assert len(MASK_LABELS) == 4
    assert actual.dtype == np.uint8
    # The original data remain unmodified and distinguish all overlapping bits.
    assert bits[1, 2] == ((1 << 12) | (1 << 3))


def test_band_specific_saturation_priority_and_no_mask_mutation():
    bits = np.array([[1 << 3, 1 << 2, 1 << 12]], dtype=np.int16)
    saved = bits.copy()
    np.testing.assert_array_equal(classify_maskbits(bits, "r"), [[3, 2, 1]])
    np.testing.assert_array_equal(classify_maskbits(bits, "g"), [[2, 3, 1]])
    np.testing.assert_array_equal(bits, saved)
    with pytest.raises(ValueError, match="band"):
        classify_maskbits(bits, "u")


def _make_original_fits(tmp_path, *, center_ra=40.096208, center_dec=1.22925):
    from astropy.io import fits
    from astropy.wcs import WCS
    from lsbpipe.surveys.legacy import BrickHit, product_for

    shape = (300, 300)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [150, 150]
    wcs.wcs.cdelt = [-1/3600, 1/3600]
    wcs.wcs.crval = [center_ra, center_dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    flux = (np.exp(-((xx-149)**2 + (yy-149)**2)/(2*12**2))).astype("f4")
    values = {
        "image":flux,
        "invvar":np.full(shape, 7., dtype="f4"),
        "nexp":np.full(shape, 4, dtype=np.int16),
        "maskbits":np.zeros(shape, dtype=np.int32),
    }
    values["maskbits"][
        (xx-149)**2+(yy-149)**2<13**2] = 1 << 12
    hit = BrickHit("dr9", "south", "0401p012", 40., 40.2, 1.1, 1.35,
                   {"r": 4})
    for kind, value in values.items():
        product = product_for(hit, kind, None if kind == "maskbits" else "r")
        target = tmp_path / product.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        fits.HDUList([
            fits.PrimaryHDU(),
            fits.CompImageHDU(data=value, header=wcs.to_header())
        ]).writeto(target)
    return tmp_path


def test_plot_real_original_coadd_uses_four_fits_and_exports_png(tmp_path):
    pytest.importorskip("astropy")
    pytest.importorskip("matplotlib")
    from lsbpipe.surveys.legacy import SkyField
    from lsbpipe.surveys.qa_plots import render_qa_figure

    folder = _make_original_fits(tmp_path)
    output = tmp_path/"evidence"/"original_fits_qa.png"
    result = render_qa_figure(
        directory=folder, release="dr9", region="south",
        brick="0401p012", band="r",
        field=SkyField(40.096208, 1.22925, 1.),
        output=output, target_radius_arcsec=10.,
        sky_inner_arcsec=20., sky_outer_arcsec=28.)
    assert result == output
    assert output.stat().st_size > 1000
    with output.open("rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_single_brick_plot_refuses_partial_field_not_zeros(tmp_path):
    pytest.importorskip("astropy")
    pytest.importorskip("matplotlib")
    from astropy.io import fits
    from astropy.wcs import WCS
    from lsbpipe.surveys.legacy import SkyField, BrickHit, product_for
    from lsbpipe.surveys.qa_plots import render_qa_figure

    _make_original_fits(tmp_path)
    hit = BrickHit("dr9", "south", "0401p012", 40., 40.2, 1.1, 1.35,
                   {"r": 4})
    source = tmp_path / product_for(hit, "image", "r").relative_path
    with fits.open(source) as hdus:
        wcs = WCS(hdus[1].header).celestial
        ra, dec = wcs.all_pix2world(3, 3, 0)
    with pytest.raises(ValueError, match="extends beyond this brick"):
        render_qa_figure(
            directory=tmp_path, release="dr9", region="south",
            brick="0401p012", band="r",
            field=SkyField(float(ra), float(dec), 1.),
            output=tmp_path/"should_not_exist.png",
            target_radius_arcsec=10.,
            sky_inner_arcsec=20., sky_outer_arcsec=28.)
    assert not (tmp_path/"should_not_exist.png").exists()
