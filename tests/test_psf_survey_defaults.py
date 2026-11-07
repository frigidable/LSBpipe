"""Legacy native PSFSIZE is an OPTIONAL per-pixel seeing prior, not ePSF."""
from pathlib import Path

import numpy as np
import pytest
pytest.importorskip("astropy")
from astropy.io import fits
from astropy.wcs import WCS

from lsbpipe.photometry.contracts import NativeBandFrame
from lsbpipe.photometry.psf_survey_defaults import (
    suggest_detection_fwhm_from_psfsize,
)


def _make_frame(tmp_path, *, write_map=True, moved_wcs=False):
    shape = (61, 61)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [31,31]
    wcs.wcs.crval = [155.438012,18.088025]
    wcs.wcs.cdelt = [-.262/3600,.262/3600]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    name = tmp_path/"legacysurvey-1554p180-image-g.fits.fz"
    name.write_bytes(b"Original image path fixture only")
    if write_map:
        header = wcs.to_header()
        if moved_wcs:
            header["CRVAL1"] += .002
        # FITS reader recognizes the content independently of extension.
        fits.PrimaryHDU(
            data=np.full(shape, 1.20, dtype=np.float32),
            header=header).writeto(
                tmp_path/"legacysurvey-1554p180-psfsize-g.fits.fz")
    frame = NativeBandFrame(
        image_nMgy=np.zeros(shape, dtype=float),
        invvar_nMgy2=np.ones(shape, dtype=float),
        nexp=np.ones(shape, dtype=np.int16),
        maskbits=np.zeros(shape, dtype=np.int16),
        native_wcs=wcs,
        release="dr11", region="south", brick="1554p180",
        band="g", source_evidence={
            "synthetic":True, "files":{"image":str(name)},
        },
    )
    return frame


def test_psfsize_map_proposes_native_fwhm_from_real_wcs(tmp_path):
    frame = _make_frame(tmp_path)
    suggestion = suggest_detection_fwhm_from_psfsize(
        frame, target_icrs_deg=(155.438012,18.088025))
    assert suggestion["available"] is True
    assert suggestion["band"] == "g"
    assert suggestion["psfsize_arcsec"] == pytest.approx(1.2,rel=1e-6)
    assert suggestion["image_pixel_scale_arcsec"] == pytest.approx(.262,rel=1e-5)
    assert suggestion["suggested_detection_fwhm_pix"] == pytest.approx(
        1.2/.262,rel=1e-5)
    assert len(suggestion["source_sha256_local"]) == 64
    assert suggestion["is_measured_empirical_psf"] is False
    assert suggestion["requires_researcher_review"] is True


def test_no_psfsize_map_never_invents_seeing(tmp_path):
    frame = _make_frame(tmp_path,write_map=False)
    suggestion = suggest_detection_fwhm_from_psfsize(
        frame, target_icrs_deg=(155.438012,18.088025))
    assert suggestion["available"] is False
    assert "ABSENT" in suggestion["source"]
    assert "suggested_detection_fwhm_pix" not in suggestion


def test_wrong_psfsize_wcs_must_fail_closed(tmp_path):
    frame = _make_frame(tmp_path,moved_wcs=True)
    with pytest.raises(ValueError, match="WCS"):
        suggest_detection_fwhm_from_psfsize(
            frame, target_icrs_deg=(155.438012,18.088025))


def _make_original_native_tractor_only(tmp_path, *, broken_map=False):
    root = tmp_path / "project-native"
    coadd = (root / "dr11" / "south" / "coadd" /
             "155" / "1554p180")
    tractor = root / "dr11" / "south" / "tractor" / "155"
    coadd.mkdir(parents=True)
    tractor.mkdir(parents=True)
    shape = (61,61)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [31,31]
    wcs.wcs.crval = [155.438012,18.088025]
    wcs.wcs.cdelt = [-.262/3600.,.262/3600.]
    wcs.wcs.ctype = ["RA---TAN","DEC--TAN"]
    image_path = coadd / "legacysurvey-1554p180-image-g.fits.fz"
    image_path.write_bytes(b"image path fixture")
    if broken_map:
        header = wcs.to_header()
        header["CRVAL1"] += .02
        fits.PrimaryHDU(
            data=np.full(shape,2.,dtype=np.float32),
            header=header).writeto(
                coadd/"legacysurvey-1554p180-psfsize-g.fits.fz")
    from astropy.table import Table
    n = 14
    rows = Table()
    rows["BRICKNAME"] = ["1554p180"]*n
    rows["BRICK_PRIMARY"] = [True]*n
    # All samples lie <90 arcsec from Leo P.
    rows["RA"] = np.full(n,155.438012) + np.linspace(-.004,.004,n)
    rows["DEC"] = np.full(n,18.088025) + np.linspace(-.004,.004,n)
    rows["PSFSIZE_G"] = np.full(n,1.20,dtype=np.float32)
    fits.HDUList([fits.PrimaryHDU(),
                  fits.BinTableHDU(rows)]).writeto(
        tractor / "tractor-1554p180.fits")
    return NativeBandFrame(
        image_nMgy=np.zeros(shape),invvar_nMgy2=np.ones(shape),
        nexp=np.ones(shape,dtype=np.int16),
        maskbits=np.zeros(shape,dtype=np.int16),
        native_wcs=wcs, release="dr11", region="south",
        brick="1554p180", band="g",
        source_evidence={
            "synthetic":True,"data_root":str(root),
            "files":{"image":str(image_path)},
        },
    )


def test_real_project_tractor_psfsize_is_explicit_backup_to_coadd_map(tmp_path):
    frame = _make_original_native_tractor_only(tmp_path)
    result = suggest_detection_fwhm_from_psfsize(
        frame,target_icrs_deg=(155.438012,18.088025))
    assert result["available"] is True
    assert result["source"] == "LOCAL_DR11_TRACTOR_NEARBY_PSF_SIZE_NOT_PIXEL_MAP"
    assert "tractor-1554p180.fits" in result["path"]
    assert result["n_tractor_psfsize_samples"] == 14
    assert result["psfsize_arcsec"] == pytest.approx(1.2)
    assert result["suggested_detection_fwhm_pix"] == pytest.approx(
        1.2/.262, rel=1e-5)
    assert result["seeing_at_target_exactly_measured"] is False
    assert "not stored" in result["native_map_status"]


def test_invalid_native_psfsize_map_is_named_in_tractor_fallback(tmp_path):
    frame = _make_original_native_tractor_only(tmp_path,broken_map=True)
    result = suggest_detection_fwhm_from_psfsize(
        frame,target_icrs_deg=(155.438012,18.088025))
    assert result["available"] is True
    assert "Native PSFSIZE map rejected" in result["native_map_status"]
    assert "WCS" in result["native_map_status"]
    assert result["source"] == "LOCAL_DR11_TRACTOR_NEARBY_PSF_SIZE_NOT_PIXEL_MAP"


def test_tractor_other_brick_identity_does_not_provide_fwhm(tmp_path):
    frame = _make_original_native_tractor_only(tmp_path)
    path=Path(frame.source_evidence["data_root"])/(
        "dr11/south/tractor/155/tractor-1554p180.fits")
    with fits.open(path) as hdus:
        tab=hdus[1].data.copy()
        tab["BRICKNAME"] = "different"
        hdr=hdus[0].header.copy()
    fits.HDUList([fits.PrimaryHDU(header=hdr),
                  fits.BinTableHDU(tab)]).writeto(path,overwrite=True)
    with pytest.raises(ValueError,match="wrong brick identity"):
        suggest_detection_fwhm_from_psfsize(
            frame,target_icrs_deg=(155.438012,18.088025))
