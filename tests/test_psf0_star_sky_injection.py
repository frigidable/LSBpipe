"""Prove with injection that local annular sky absorbs Moffat wings."""
import numpy as np
import pytest
pytest.importorskip("astropy")
from astropy.wcs import WCS

from lsbpipe.photometry.contracts import NativeBandFrame
from lsbpipe.photometry.psf_empirical import PSF0Config, _star_cutout


def _native_wcs(shape):
    w = WCS(naxis=2)
    w.wcs.crpix = [shape[1]/2+.5, shape[0]/2+.5]
    w.wcs.crval = [155.438012, 18.088025]
    w.wcs.cdelt = [-.262/3600.,.262/3600.]
    w.wcs.ctype = ["RA---TAN","DEC--TAN"]
    return w


def test_moffat_wings_bias_annular_background_even_with_sigma_clipping():
    shape=(111,111)
    yy,xx=np.indices(shape,dtype=float)
    rr2=(xx-55.)**2+(yy-55.)**2
    # True constant sky 0.05 nMgy/pixel, PSF has extended Moffat wings.
    true_sky=.05
    signal=100.*(1+rr2/(4.3**2))**(-2.5)
    image=true_sky+signal
    frame=NativeBandFrame(
        image_nMgy=image,
        invvar_nMgy2=np.full(shape,100.),
        nexp=np.full(shape,2,dtype=np.int16),
        maskbits=np.zeros(shape,dtype=np.int16),
        native_wcs=_native_wcs(shape),release="dr11",region="south",
        brick="1554p180",band="g",
        source_evidence={"synthetic":True})
    protect=np.zeros(shape,dtype=bool)
    inner,reason=_star_cutout(
        frame,55.,55.,PSF0Config(),protect)
    assert reason is None
    wide,reason=_star_cutout(
        frame,55.,55.,PSF0Config(
            cutout_size_pix=51,sky_annulus_inner_pix=19.,
            sky_annulus_outer_pix=24.,
            min_neighbor_separation_pix=55.),protect)
    assert reason is None
    # The annular "sky" is higher than the true sky purely because it
    # includes star wings. Enlarging the cutout helps but cannot establish
    # a properly separated astrophysical background.
    assert inner["sky_nMgy"] > wide["sky_nMgy"] > true_sky
    assert inner["sky_qa"]["wing_sky_degeneracy_warning"]
    assert wide["sky_qa"]["wing_sky_degeneracy_warning"]
    assert not wide["sky_qa"]["sky_versus_psf_wings_separable_from_this_cutout"]
